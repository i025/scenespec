import math
from collections import deque

def layout(packet):
    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    fixed = list(packet['fixed'])
    fixed_by_id = {o['id']: o for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    names = sorted(n for n in required if n not in fixed_by_id)
    constraints = spec.get('constraints', [])
    regions = {r['id']: r for r in spec.get('regions', [])}
    relations = [c for c in constraints if c['type'] == 'in_front_of']
    walls = [c for c in constraints if c['type'] == 'near_wall']
    clear_regions = []
    for c in constraints:
        if c['type'] == 'keep_clear':
            r = regions[c['region']]
            clear_regions.append((r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth']))

    def rectangle(o):
        a = assets[o['asset']]
        w, d = a['width'], a['depth']
        if o['yaw_deg'] % 180:
            w, d = d, w
        return (o['x'] - w / 2, o['y'] - d / 2, o['x'] + w / 2, o['y'] + d / 2)

    def overlap(a, b):
        return min(a[2], b[2]) - max(a[0], b[0]) > 1e-8 and min(a[3], b[3]) - max(a[1], b[1]) > 1e-8

    def front(subject, sr, reference, rr, gap_limit):
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[int(reference['yaw_deg'] % 360) // 90]
        dx, dy = subject['x'] - reference['x'], subject['y'] - reference['y']
        hx = (sr[2] - sr[0] + rr[2] - rr[0]) / 2
        hy = (sr[3] - sr[1] + rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * hx - abs(fy) * hy
        lateral = abs(dx * fy - dy * fx)
        return -1e-8 <= gap <= gap_limit + 1e-8 and lateral <= abs(fy) * hx + abs(fx) * hy + 1e-8

    fixed_rects = [rectangle(o) for o in fixed]
    poses, rects = [], []
    for name in names:
        options, boxes, seen = [], [], set()
        source = sorted(packet['domains'].get(name, []), key=lambda o: (o['x'], o['y'], o['yaw_deg']))
        for o in source:
            if o['id'] != name or o['asset'] != required[name]:
                continue
            key = (o['x'], o['y'], o['yaw_deg'])
            if key in seen:
                continue
            seen.add(key)
            r = rectangle(o)
            if r[0] < -1e-8 or r[1] < -1e-8 or r[2] > width + 1e-8 or r[3] > depth + 1e-8:
                continue
            if any(overlap(r, z) for z in fixed_rects + clear_regions):
                continue
            valid = True
            for c in walls:
                if c['subject'] == name:
                    gap = {'west': r[0], 'south': r[1], 'east': width - r[2], 'north': depth - r[3]}[c['wall']]
                    if not -1e-8 <= gap <= c['max_gap'] + 1e-8:
                        valid = False
                        break
            if not valid:
                continue
            for c in relations:
                if c['subject'] == name and c['reference'] in fixed_by_id:
                    ref = fixed_by_id[c['reference']]
                    if not front(o, r, ref, rectangle(ref), c['max_gap']):
                        valid = False
                        break
                if c['reference'] == name and c['subject'] in fixed_by_id:
                    sub = fixed_by_id[c['subject']]
                    if not front(sub, rectangle(sub), o, r, c['max_gap']):
                        valid = False
                        break
            if valid:
                options.append(o)
                boxes.append(r)
        poses.append(options)
        rects.append(boxes)

    def grid(scale):
        w, h = int(round(width * scale)), int(round(depth * scale))
        all_cells = (1 << (w * h)) - 1
        left = sum(1 << (y * w) for y in range(h))
        right = left << (w - 1)
        return w, h, all_cells, all_cells ^ left, all_cells ^ right

    navigation = grid(1)
    fine_grid = grid(5)

    def neighbors(bits, g):
        w, h, all_cells, not_left, not_right = g
        return (((bits & not_right) << 1) | ((bits & not_left) >> 1) | (bits << w) | (bits >> w)) & all_cells

    def blocked_rectangle(r, clearance, scale, g):
        w, h = g[0], g[1]
        x0 = max(0, math.floor((r[0] - clearance) * scale - 0.5) + 1)
        x1 = min(w - 1, math.ceil((r[2] + clearance) * scale - 0.5) - 1)
        y0 = max(0, math.floor((r[1] - clearance) * scale - 0.5) + 1)
        y1 = min(h - 1, math.ceil((r[3] + clearance) * scale - 0.5) - 1)
        if x1 < x0 or y1 < y0:
            return 0
        row = ((1 << (x1 - x0 + 1)) - 1) << x0
        result = 0
        for y in range(y0, y1 + 1):
            result |= row << (y * w)
        return result

    w = navigation[0]
    start = 1 << (packet['start'][1] * w + packet['start'][0])
    goal = 1 << (packet['goal'][1] * w + packet['goal'][0])
    endpoints = start | goal
    minimum = packet['minimum_steps']
    fixed_mask = 0
    for r in fixed_rects:
        fixed_mask |= blocked_rectangle(r, 0.15, 1, navigation)
    masks = [[blocked_rectangle(r, 0.15, 1, navigation) for r in rs] for rs in rects]
    n = len(names)
    domains = []
    for i in range(n):
        mask = 0
        for a in range(len(poses[i])):
            if not masks[i][a] & endpoints:
                mask |= 1 << a
        domains.append(mask)

    pair_relations = {}
    index = {name: i for i, name in enumerate(names)}
    for c in relations:
        if c['subject'] in index and c['reference'] in index:
            i, j = index[c['subject']], index[c['reference']]
            pair_relations.setdefault((min(i, j), max(i, j)), []).append(c)
    support = [[None for j in range(n)] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            forward = [0] * len(poses[i])
            backward = [0] * len(poses[j])
            rels = pair_relations.get((i, j), [])
            for a, o in enumerate(poses[i]):
                for b, other in enumerate(poses[j]):
                    if overlap(rects[i][a], rects[j][b]):
                        continue
                    valid = True
                    for c in rels:
                        if c['subject'] == names[i]:
                            valid = front(o, rects[i][a], other, rects[j][b], c['max_gap'])
                        else:
                            valid = front(other, rects[j][b], o, rects[i][a], c['max_gap'])
                        if not valid:
                            break
                    if valid:
                        forward[a] |= 1 << b
                        backward[b] |= 1 << a
            support[i][j], support[j][i] = forward, backward

    work = [0, 0]
    def propagate(ds, changed):
        if any(v == 0 for v in ds):
            return False
        queue = deque((j, i) for i in changed for j in range(n) if j != i)
        while queue:
            i, j = queue.popleft()
            old = ds[i]
            remaining, new = old, 0
            table = support[i][j]
            while remaining:
                bit = remaining & -remaining
                remaining -= bit
                work[0] += 1
                if table[bit.bit_length() - 1] & ds[j]:
                    new |= bit
            if new != old:
                if not new:
                    return False
                ds[i] = new
                queue.extend((k, i) for k in range(n) if k != i and k != j)
        return True

    nav_cache = {}
    def walk(blocked):
        if blocked in nav_cache:
            return nav_cache[blocked]
        if blocked & endpoints:
            return (-1, 0)
        free = navigation[2] ^ blocked
        reached, frontier = start, start
        layers = [start]
        distance = 0
        while not frontier & goal:
            frontier = neighbors(frontier, navigation) & free & ~reached
            if not frontier:
                result = (-1, 0)
                break
            reached |= frontier
            layers.append(frontier)
            distance += 1
        else:
            path, cell = goal, goal
            for layer in reversed(layers[:-1]):
                choices = neighbors(cell, navigation) & layer
                cell = choices & -choices
                path |= cell
            result = (distance, path)
        if len(nav_cache) >= 4096:
            nav_cache.clear()
        nav_cache[blocked] = result
        return result

    access = spec.get('access_paths', [])
    clearances = sorted(set(a.get('clearance', 0.15) for a in access))
    fine_fixed, fine_masks = {}, {}
    for clearance in clearances:
        total = 0
        for r in fixed_rects:
            total |= blocked_rectangle(r, clearance, 5, fine_grid)
        fine_fixed[clearance] = total
        fine_masks[clearance] = [[blocked_rectangle(r, clearance, 5, fine_grid) for r in rs] for rs in rects]
    access_targets = {}
    access_sources = {}
    for a in access:
        rid = a['from_region']
        if rid not in access_sources:
            region = regions[rid]
            cells = 0
            for y in range(fine_grid[1]):
                py = (y + 0.5) / 5
                for x in range(fine_grid[0]):
                    px = (x + 0.5) / 5
                    if region['x'] <= px <= region['x'] + region['width'] and region['y'] <= py <= region['y'] + region['depth']:
                        cells |= 1 << (y * fine_grid[0] + x)
            access_sources[rid] = cells
        name = a['to_object']
        options = [fixed_by_id[name]] if name in fixed_by_id else poses[index[name]]
        distance = a.get('interaction_distance', 0.8)
        key = (name, distance)
        if key not in access_targets:
            targets = []
            for o in options:
                r = rectangle(o)
                cells = 0
                for y in range(fine_grid[1]):
                    py = (y + 0.5) / 5
                    dy = max(r[1] - py, 0, py - r[3])
                    for x in range(fine_grid[0]):
                        px = (x + 0.5) / 5
                        dx = max(r[0] - px, 0, px - r[2])
                        if dx * dx + dy * dy <= distance * distance + 1e-8:
                            cells |= 1 << (y * fine_grid[0] + x)
                targets.append(cells)
            access_targets[key] = targets

    def accessible(chosen):
        floods = {}
        for a in access:
            clearance = a.get('clearance', 0.15)
            key = (clearance, a['from_region'])
            if key not in floods:
                blocked = fine_fixed[clearance]
                for i, value in enumerate(chosen):
                    blocked |= fine_masks[clearance][i][value]
                free = fine_grid[2] ^ blocked
                reached = access_sources[a['from_region']] & free
                frontier = reached
                while frontier:
                    frontier = neighbors(frontier, fine_grid) & free & ~reached
                    reached |= frontier
                floods[key] = reached
            name = a['to_object']
            value = 0 if name in fixed_by_id else chosen[index[name]]
            targets = access_targets[(name, a.get('interaction_distance', 0.8))][value]
            if not floods[key] & targets:
                return False
        return True

    best = [None, -1]
    def search(ds):
        work[1] += 1
        if work[1] > 22000 or work[0] > 2400000:
            return None
        blocked = fixed_mask
        unresolved = []
        for i, values in enumerate(ds):
            remaining, mandatory = values, navigation[2]
            while remaining:
                bit = remaining & -remaining
                remaining -= bit
                mandatory &= masks[i][bit.bit_length() - 1]
            blocked |= mandatory
            if values & (values - 1):
                unresolved.append(i)
        distance, path = walk(blocked)
        if distance < 0:
            return None
        if not unresolved:
            chosen = [v.bit_length() - 1 for v in ds]
            if accessible(chosen):
                if distance > best[1]:
                    best[0], best[1] = chosen, distance
                if distance >= minimum:
                    return chosen
            return None
        hits = {}
        if distance < minimum:
            for i in unresolved:
                values, hit = ds[i], 0
                while values:
                    bit = values & -values
                    values -= bit
                    if masks[i][bit.bit_length() - 1] & path:
                        hit |= bit
                if hit:
                    hits[i] = hit
            if best[0] is not None:
                if not hits:
                    return None
                if len(hits) == 1:
                    i = next(iter(hits))
                    if hits[i] != ds[i]:
                        child = ds[:]
                        child[i] = hits[i]
                        if propagate(child, [i]):
                            return search(child)
                        return None
        i = min(unresolved, key=lambda j: (ds[j].bit_count(), -hits.get(j, 0).bit_count(), -len(pair_relations.get((j, j), []))))
        order = []
        values = ds[i]
        while values:
            bit = values & -values
            values -= bit
            value = bit.bit_length() - 1
            d, candidate_path = walk(blocked | masks[i][value])
            if d >= 0:
                order.append((d, (masks[i][value] & path).bit_count(), value))
        order.sort(reverse=True)
        for d, impact, value in order:
            child = ds[:]
            child[i] = 1 << value
            if propagate(child, [i]):
                result = search(child)
                if result is not None:
                    return result
            if work[1] > 22000 or work[0] > 2400000:
                break
        return None

    result = None
    if not fixed_mask & endpoints and propagate(domains, range(n)):
        result = search(domains)
    if result is None:
        result = best[0]
    objects = list(fixed)
    if result is not None:
        objects.extend(poses[i][value] for i, value in enumerate(result))
    else:
        for i, name in enumerate(names):
            if poses[i]:
                objects.append(poses[i][0])
            elif packet['domains'].get(name):
                objects.append(packet['domains'][name][0])
    return {'scene_id': spec['scene_id'], 'objects': objects}
