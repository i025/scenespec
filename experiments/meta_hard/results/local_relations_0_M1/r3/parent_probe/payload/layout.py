import math
from collections import deque

def layout(packet):
    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    fixed = list(packet['fixed'])
    fixed_by_id = {o['id']: o for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    names = sorted(k for k in required if k not in fixed_by_id)
    index = {name: i for i, name in enumerate(names)}
    n = len(names)
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

    def front(subject, sr, reference, rr, limit):
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[int(reference['yaw_deg'] % 360) // 90]
        dx, dy = subject['x'] - reference['x'], subject['y'] - reference['y']
        hx = (sr[2] - sr[0] + rr[2] - rr[0]) / 2
        hy = (sr[3] - sr[1] + rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * hx - abs(fy) * hy
        return -1e-8 <= gap <= limit + 1e-8 and abs(dx * fy - dy * fx) <= abs(fy) * hx + abs(fx) * hy + 1e-8

    fixed_rects = [rectangle(o) for o in fixed]
    fixed_boxes = {o['id']: r for o, r in zip(fixed, fixed_rects)}
    reference_names = {c['reference'] for c in relations}
    poses, rects = [], []
    for name in names:
        options, boxes, seen = [], [], set()
        for o in sorted(packet['domains'].get(name, []), key=lambda p: (p['x'], p['y'], p['yaw_deg'])):
            if o['id'] != name or o['asset'] != required[name]:
                continue
            r = rectangle(o)
            key = (r, o['yaw_deg'] % 360 if name in reference_names else 0)
            if key in seen:
                continue
            seen.add(key)
            if r[0] < -1e-8 or r[1] < -1e-8 or r[2] > width + 1e-8 or r[3] > depth + 1e-8:
                continue
            if any(overlap(r, z) for z in fixed_rects) or any(overlap(r, z) for z in clear_regions):
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
                    ref = c['reference']
                    if not front(o, r, fixed_by_id[ref], fixed_boxes[ref], c['max_gap']):
                        valid = False
                        break
                if c['reference'] == name and c['subject'] in fixed_by_id:
                    sub = c['subject']
                    if not front(fixed_by_id[sub], fixed_boxes[sub], o, r, c['max_gap']):
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
        return (w, h, all_cells, all_cells ^ left, all_cells ^ (left << (w - 1)))

    navigation = grid(1)
    fine_grid = grid(5)

    def neighbors(bits, g):
        return (((bits & g[4]) << 1) | ((bits & g[3]) >> 1) | (bits << g[0]) | (bits >> g[0])) & g[2]

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
    domains = []
    for i in range(n):
        values = 0
        for a, mask in enumerate(masks[i]):
            if not mask & endpoints:
                values |= 1 << a
        domains.append(values)

    pair_relations = {}
    degree = [0] * n
    for c in relations:
        if c['subject'] in index and c['reference'] in index:
            i, j = index[c['subject']], index[c['reference']]
            pair_relations.setdefault((min(i, j), max(i, j)), []).append(c)
            degree[i] += 1
            degree[j] += 1
    support = [[None] * n for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            forward = [0] * len(poses[i])
            backward = [0] * len(poses[j])
            rels = pair_relations.get((i, j), [])
            for a, o in enumerate(poses[i]):
                if not domains[i] & (1 << a):
                    continue
                for b, other in enumerate(poses[j]):
                    if not domains[j] & (1 << b) or overlap(rects[i][a], rects[j][b]):
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

    work = [0, 0, 0]
    limits = [3200000, 36000, 1400000]

    def exhausted():
        return work[0] >= limits[0] or work[1] >= limits[1] or work[2] >= limits[2]

    def propagate(ds, changed):
        if any(v == 0 for v in ds):
            return False
        queue = deque((j, i) for i in changed for j in range(n) if j != i)
        pending = set(queue)
        while queue:
            i, j = queue.popleft()
            pending.discard((i, j))
            old, new = ds[i], 0
            remaining = old
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
                for k in range(n):
                    arc = (k, i)
                    if k != i and k != j and arc not in pending:
                        pending.add(arc)
                        queue.append(arc)
            if work[0] >= limits[0]:
                return False
        return True

    nav_cache = {}

    def walk(blocked):
        cached = nav_cache.get(blocked)
        if cached is not None:
            return cached
        if blocked & endpoints:
            return (-1, 0)
        free = navigation[2] & ~blocked
        reached, frontier = start, start
        layers = [start]
        distance = 0
        while not frontier & goal:
            work[2] += 1
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
        if len(nav_cache) >= 8192:
            nav_cache.clear()
        nav_cache[blocked] = result
        return result

    access = spec.get('access_paths', [])
    clearances = sorted({a.get('clearance', 0.15) for a in access})
    fine_fixed, fine_masks = {}, {}
    for clearance in clearances:
        total = 0
        for r in fixed_rects:
            total |= blocked_rectangle(r, clearance, 5, fine_grid)
        fine_fixed[clearance] = total
        fine_masks[clearance] = [[blocked_rectangle(r, clearance, 5, fine_grid) for r in rs] for rs in rects]
    access_sources = {}
    for a in access:
        rid = a['from_region']
        if rid in access_sources:
            continue
        region = regions[rid]
        cells = 0
        for y in range(fine_grid[1]):
            py = (y + 0.5) / 5
            if region['y'] <= py <= region['y'] + region['depth']:
                for x in range(fine_grid[0]):
                    px = (x + 0.5) / 5
                    if region['x'] <= px <= region['x'] + region['width']:
                        cells |= 1 << (y * fine_grid[0] + x)
        access_sources[rid] = cells
    access_targets = {}

    def target_cells(name, value, distance):
        key = (name, value, distance)
        if key in access_targets:
            return access_targets[key]
        r = fixed_boxes[name] if name in fixed_by_id else rects[index[name]][value]
        cells = 0
        x0 = max(0, math.floor((r[0] - distance) * 5 - 0.5))
        x1 = min(fine_grid[0] - 1, math.ceil((r[2] + distance) * 5 - 0.5))
        y0 = max(0, math.floor((r[1] - distance) * 5 - 0.5))
        y1 = min(fine_grid[1] - 1, math.ceil((r[3] + distance) * 5 - 0.5))
        for y in range(y0, y1 + 1):
            py = (y + 0.5) / 5
            dy = max(r[1] - py, 0, py - r[3])
            for x in range(x0, x1 + 1):
                px = (x + 0.5) / 5
                dx = max(r[0] - px, 0, px - r[2])
                if dx * dx + dy * dy <= distance * distance + 1e-8:
                    cells |= 1 << (y * fine_grid[0] + x)
        access_targets[key] = cells
        return cells

    access_cache = {}

    def accessible(chosen):
        floods = {}
        for a in access:
            clearance = a.get('clearance', 0.15)
            rid = a['from_region']
            key = (clearance, rid)
            if key not in floods:
                blocked = fine_fixed[clearance]
                for i, value in enumerate(chosen):
                    blocked |= fine_masks[clearance][i][value]
                cache_key = (clearance, rid, blocked)
                if cache_key in access_cache:
                    reached = access_cache[cache_key]
                else:
                    free = fine_grid[2] & ~blocked
                    reached = access_sources[rid] & free
                    frontier = reached
                    while frontier:
                        work[2] += 1
                        frontier = neighbors(frontier, fine_grid) & free & ~reached
                        reached |= frontier
                    if len(access_cache) >= 128:
                        access_cache.clear()
                    access_cache[cache_key] = reached
                floods[key] = reached
            name = a['to_object']
            value = 0 if name in fixed_by_id else chosen[index[name]]
            if not floods[key] & target_cells(name, value, a.get('interaction_distance', 0.8)):
                return False
        return True

    best = [None, -1]

    def summarize(ds):
        blocked = fixed_mask
        unresolved = []
        for i, values in enumerate(ds):
            remaining, mandatory = values, navigation[2]
            while remaining:
                bit = remaining & -remaining
                remaining -= bit
                mandatory &= masks[i][bit.bit_length() - 1]
                if not mandatory:
                    break
            blocked |= mandatory
            if values & (values - 1):
                unresolved.append(i)
        return blocked, unresolved

    def ordered_values(i, values, blocked, path):
        order = []
        while values:
            bit = values & -values
            values -= bit
            value = bit.bit_length() - 1
            distance, unused = walk(blocked | masks[i][value])
            if distance >= 0:
                flexibility = 0
                order.append((distance, (masks[i][value] & path).bit_count(), flexibility, value))
        order.sort(reverse=True)
        return order

    def search(ds, require_minimum):
        if exhausted():
            return None
        work[1] += 1
        blocked, unresolved = summarize(ds)
        distance, path = walk(blocked)
        if distance < 0:
            return None
        if not unresolved:
            if require_minimum and distance < minimum:
                return None
            chosen = [v.bit_length() - 1 for v in ds]
            if accessible(chosen):
                if distance > best[1]:
                    best[0], best[1] = chosen, distance
                return chosen
            return None

        if require_minimum and distance < minimum:
            # Every acceptable completion must block this actual short path.
            # Partition completions by their first object that blocks it.
            hits = []
            for i in unresolved:
                values, hit = ds[i], 0
                while values:
                    bit = values & -values
                    values -= bit
                    if masks[i][bit.bit_length() - 1] & path:
                        hit |= bit
                if hit:
                    hits.append((i, hit))
            if not hits:
                return None
            hits.sort(key=lambda p: (p[1].bit_count(), ds[p[0]].bit_count(), -degree[p[0]]))
            remaining_domains = ds[:]
            for i, original_hit in hits:
                hit = original_hit & remaining_domains[i]
                if not hit:
                    continue
                for d, impact, flexibility, value in ordered_values(i, hit, blocked, path):
                    if exhausted():
                        return None
                    child = remaining_domains[:]
                    child[i] = 1 << value
                    if propagate(child, [i]):
                        result = search(child, True)
                        if result is not None:
                            return result
                # Later branches cannot select any earlier path-blocking pose.
                remaining_domains[i] &= ~hit
                if not remaining_domains[i] or exhausted():
                    return None
                if not propagate(remaining_domains, [i]):
                    return None
            return None

        i = min(unresolved, key=lambda j: (ds[j].bit_count(), -degree[j]))
        for d, impact, flexibility, value in ordered_values(i, ds[i], blocked, path):
            if exhausted():
                break
            child = ds[:]
            child[i] = 1 << value
            if propagate(child, [i]):
                result = search(child, require_minimum)
                if result is not None:
                    return result
        return None

    result = None
    if not fixed_mask & endpoints and propagate(domains, range(n)):
        root = domains[:]
        # First retain a complete geometrically compatible, accessible fallback.
        limits[:] = [min(3200000, work[0] + 180000), 1500, 100000]
        candidate = search(root[:], False)
        if candidate is not None and best[1] >= minimum:
            result = candidate
        if result is None:
            limits[:] = [3200000, 36000, 1400000]
            result = search(root[:], True)
    if result is None:
        result = best[0]
    objects = list(fixed)
    if result is not None:
        objects.extend(poses[i][value] for i, value in enumerate(result))
    else:
        # Budget exhaustion cannot establish feasibility; preserve supplied poses.
        for i, name in enumerate(names):
            if poses[i]:
                objects.append(poses[i][0])
            elif packet['domains'].get(name):
                objects.append(packet['domains'][name][0])
    return {'scene_id': spec['scene_id'], 'objects': objects}
