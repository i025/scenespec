import math
from collections import deque


def layout(packet):
    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    fixed = list(packet['fixed'])
    fixed_by_id = {o['id']: o for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    constraints = spec.get('constraints', [])
    regions = {r['id']: r for r in spec.get('regions', [])}
    relations = [c for c in constraints if c['type'] == 'in_front_of']
    referenced = {c['reference'] for c in relations}
    walls = {}
    forbidden = []
    for c in constraints:
        if c['type'] == 'near_wall':
            walls.setdefault(c['subject'], []).append(c)
        elif c['type'] == 'keep_clear':
            r = regions[c['region']]
            forbidden.append((r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth']))

    def rectangle(o):
        a = assets[o['asset']]
        w, h = a['width'], a['depth']
        if o['yaw_deg'] % 180:
            w, h = h, w
        return (o['x'] - w / 2, o['y'] - h / 2,
                o['x'] + w / 2, o['y'] + h / 2)

    def overlap(a, b):
        return (min(a[2], b[2]) - max(a[0], b[0]) > 1e-8 and
                min(a[3], b[3]) - max(a[1], b[1]) > 1e-8)

    def front(subject, sr, reference, rr, limit):
        fx, fy = {0: (0, 1), 90: (-1, 0), 180: (0, -1), 270: (1, 0)}[reference['yaw_deg'] % 360]
        dx = subject['x'] - reference['x']
        dy = subject['y'] - reference['y']
        sx, sy = (sr[2] - sr[0]) / 2, (sr[3] - sr[1]) / 2
        rx, ry = (rr[2] - rr[0]) / 2, (rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * (sx + rx) - abs(fy) * (sy + ry)
        return (-1e-8 <= gap <= limit + 1e-8 and
                abs(dx * fy - dy * fx) <= abs(fy) * (sx + rx) + abs(fx) * (sy + ry) + 1e-8)

    def related(a, ar, b, br, rules):
        for c in rules:
            if c['subject'] == a['id']:
                if not front(a, ar, b, br, c['max_gap']):
                    return False
            elif not front(b, br, a, ar, c['max_gap']):
                return False
        return True

    nx, ny = int(width), int(depth)
    cells = nx * ny
    all_cells = (1 << cells) - 1
    start = int(packet['start'][1]) * nx + int(packet['start'][0])
    goal = int(packet['goal'][1]) * nx + int(packet['goal'][0])
    source_bit, goal_bit = 1 << start, 1 << goal
    endpoints = source_bit | goal_bit
    left_edge = sum(1 << (y * nx) for y in range(ny))
    right_edge = left_edge << (nx - 1)
    move_right = all_cells ^ right_edge
    move_left = all_cells ^ left_edge
    minimum = packet['minimum_steps']
    budget = [0, 0, 0]
    distance_cache = {}

    def raster(r, clearance, gx, gy, step):
        row = 0
        for x in range(gx):
            px = (x + 0.5) * step
            if r[0] - clearance < px < r[2] + clearance:
                row |= 1 << x
        mask = 0
        if row:
            for y in range(gy):
                py = (y + 0.5) * step
                if r[1] - clearance < py < r[3] + clearance:
                    mask |= row << (y * gx)
        return mask

    def adjacent(mask):
        return (((mask & move_right) << 1) |
                ((mask & move_left) >> 1) |
                (mask << nx) | (mask >> nx)) & all_cells

    def navigation(blocked, need_path=False):
        cached = distance_cache.get(blocked)
        if cached is not None and (not need_path or cached[1] or cached[0] < 0):
            return cached
        if blocked & endpoints:
            return (-1, 0)
        free = all_cells & ~blocked
        frontier = source_bit
        seen = frontier
        layers = [frontier] if need_path else []
        steps = 0
        while frontier:
            budget[2] += 1
            if frontier & goal_bit:
                path = 0
                if need_path:
                    current = goal_bit
                    path = current
                    for level in range(steps - 1, -1, -1):
                        previous = adjacent(current) & layers[level]
                        current = previous & -previous
                        path |= current
                result = (steps, path)
                if len(distance_cache) < 18000:
                    distance_cache[blocked] = result
                return result
            frontier = adjacent(frontier) & free & ~seen
            seen |= frontier
            steps += 1
            if need_path:
                layers.append(frontier)
        result = (-1, 0)
        if len(distance_cache) < 18000:
            distance_cache[blocked] = result
        return result

    fixed_rects = [rectangle(o) for o in fixed]
    fixed_blocked = 0
    for r in fixed_rects:
        fixed_blocked |= raster(r, 0.15, nx, ny, 1.0)

    names = sorted(name for name in required if name not in fixed_by_id)
    poses, rects, blocks = [], [], []
    for name in names:
        options, boxes, masks = [], [], []
        seen = set()
        for o in packet['domains'].get(name, []):
            if o['id'] != name or o['asset'] != required[name]:
                continue
            if o['yaw_deg'] % 360 not in (0, 90, 180, 270):
                continue
            r = rectangle(o)
            if r[0] < -1e-8 or r[1] < -1e-8 or r[2] > width + 1e-8 or r[3] > depth + 1e-8:
                continue
            if any(overlap(r, z) for z in forbidden) or any(overlap(r, z) for z in fixed_rects):
                continue
            good = True
            for c in walls.get(name, []):
                gap = {'west': r[0], 'east': width - r[2],
                       'south': r[1], 'north': depth - r[3]}[c['wall']]
                if not -1e-8 <= gap <= c['max_gap'] + 1e-8:
                    good = False
                    break
            if not good:
                continue
            for c in relations:
                if c['subject'] == name and c['reference'] in fixed_by_id:
                    ref = fixed_by_id[c['reference']]
                    if not front(o, r, ref, rectangle(ref), c['max_gap']):
                        good = False
                        break
                if c['reference'] == name and c['subject'] in fixed_by_id:
                    sub = fixed_by_id[c['subject']]
                    if not front(sub, rectangle(sub), o, r, c['max_gap']):
                        good = False
                        break
            if not good:
                continue
            mask = raster(r, 0.15, nx, ny, 1.0)
            if mask & endpoints:
                continue
            key = (r, o['yaw_deg'] % 360 if name in referenced else 0)
            if key in seen:
                continue
            seen.add(key)
            options.append(o)
            boxes.append(r)
            masks.append(mask)
        order = sorted(range(len(options)), key=lambda k: (boxes[k], options[k]['yaw_deg']))
        poses.append([options[k] for k in order])
        rects.append([boxes[k] for k in order])
        blocks.append([masks[k] for k in order])

    n = len(names)
    compatible = [[None for j in range(n)] for i in range(n)]
    neighbors = [[] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            rules = [c for c in relations if
                     (c['subject'] == names[i] and c['reference'] == names[j]) or
                     (c['subject'] == names[j] and c['reference'] == names[i])]
            forward = [0] * len(poses[i])
            backward = [0] * len(poses[j])
            restricted = False
            for a, oa in enumerate(poses[i]):
                for b, ob in enumerate(poses[j]):
                    if (not overlap(rects[i][a], rects[j][b]) and
                            related(oa, rects[i][a], ob, rects[j][b], rules)):
                        forward[a] |= 1 << b
                        backward[b] |= 1 << a
                    else:
                        restricted = True
            if restricted:
                compatible[i][j] = forward
                compatible[j][i] = backward
                neighbors[i].append(j)
                neighbors[j].append(i)

    def exhausted():
        return budget[0] >= 32000 or budget[1] >= 650000 or budget[2] >= 1600000

    def propagate(domains, queue):
        while queue:
            i, j = queue.popleft()
            budget[1] += 1
            if budget[1] >= 650000:
                return False
            remaining = domains[i]
            revised = 0
            support = compatible[i][j]
            while remaining:
                bit = remaining & -remaining
                remaining -= bit
                if support[bit.bit_length() - 1] & domains[j]:
                    revised |= bit
            if not revised:
                return False
            if revised != domains[i]:
                domains[i] = revised
                for k in neighbors[i]:
                    if k != j:
                        queue.append((k, i))
        return True

    access = spec.get('access_paths', [])
    step = 0.2
    ax = max(1, int(math.ceil(width / step)))
    ay = max(1, int(math.ceil(depth / step)))
    access_all = (1 << (ax * ay)) - 1
    access_left = sum(1 << (y * ax) for y in range(ay))
    access_right = access_left << (ax - 1)
    access_rm = access_all ^ access_right
    access_lm = access_all ^ access_left
    access_masks = {}
    access_sources = {}
    approach_masks = {}
    access_valid_cells = 0
    for y in range(ay):
        if (y + 0.5) * step < depth:
            for x in range(ax):
                if (x + 0.5) * step < width:
                    access_valid_cells |= 1 << (y * ax + x)

    def accessible(selection):
        boxes = {o['id']: r for o, r in zip(fixed, fixed_rects)}
        for i, a in enumerate(selection):
            boxes[names[i]] = rects[i][a]
        floods = {}
        for path in access:
            clearance = path.get('clearance', 0.15)
            region_id = path['from_region']
            flood_key = (clearance, region_id)
            if flood_key not in floods:
                blocked = 0
                for r in boxes.values():
                    key = (r, clearance)
                    if key not in access_masks:
                        access_masks[key] = raster(r, clearance, ax, ay, step)
                    blocked |= access_masks[key]
                free = access_valid_cells & ~blocked
                if region_id not in access_sources:
                    region = regions[region_id]
                    source = 0
                    for y in range(ay):
                        py = (y + 0.5) * step
                        if not region['y'] <= py <= region['y'] + region['depth']:
                            continue
                        for x in range(ax):
                            px = (x + 0.5) * step
                            if region['x'] <= px <= region['x'] + region['width'] and px < width and py < depth:
                                source |= 1 << (y * ax + x)
                    access_sources[region_id] = source
                seen = access_sources[region_id] & free
                frontier = seen
                while frontier:
                    frontier = (((frontier & access_rm) << 1) |
                                ((frontier & access_lm) >> 1) |
                                (frontier << ax) | (frontier >> ax)) & free & ~seen
                    seen |= frontier
                floods[flood_key] = seen
            r = boxes[path['to_object']]
            reach = path.get('interaction_distance', 0.8)
            key = (r, reach)
            if key not in approach_masks:
                target = 0
                for y in range(ay):
                    py = (y + 0.5) * step
                    if py >= depth:
                        continue
                    dy = max(r[1] - py, 0.0, py - r[3])
                    if dy > reach:
                        continue
                    for x in range(ax):
                        px = (x + 0.5) * step
                        if px >= width:
                            continue
                        dx = max(r[0] - px, 0.0, px - r[2])
                        if dx * dx + dy * dy <= reach * reach + 1e-8:
                            target |= 1 << (y * ax + x)
                approach_masks[key] = target
            if not floods[flood_key] & approach_masks[key]:
                return False
        return True

    initial = [(1 << len(v)) - 1 for v in poses]
    initial_queue = deque((i, j) for i in range(n) for j in neighbors[i])
    root_ok = all(initial) and propagate(initial, initial_queue)
    best = [None, -1]
    leaf_cache = {}
    domain_geometry = [{} for i in range(n)]

    def geometry(i, domain):
        cached = domain_geometry[i].get(domain)
        if cached is not None:
            return cached
        common, union = all_cells, 0
        bits = domain
        while bits:
            bit = bits & -bits
            bits -= bit
            mask = blocks[i][bit.bit_length() - 1]
            common &= mask
            union |= mask
        result = (common, union)
        if len(domain_geometry[i]) < 3000:
            domain_geometry[i][domain] = result
        return result

    def search(domains, hard, limit):
        if exhausted() or budget[0] >= limit:
            return None
        budget[0] += 1
        lower, upper = fixed_blocked, fixed_blocked
        variable = -1
        priority = None
        for i, domain in enumerate(domains):
            common, union = geometry(i, domain)
            lower |= common
            upper |= union
            count = domain.bit_count()
            if count > 1:
                degree = sum(1 for j in neighbors[i] if domains[j].bit_count() > 1)
                score = (count, -degree)
                if priority is None or score < priority:
                    priority, variable = score, i
        shortest, unused_path = navigation(lower)
        if shortest < 0:
            return None
        if variable < 0:
            selection = [domain.bit_length() - 1 for domain in domains]
            key = tuple(selection)
            good = leaf_cache.get(key)
            if good is None:
                good = accessible(selection)
                if len(leaf_cache) < 6000:
                    leaf_cache[key] = good
            if not good:
                return None
            if shortest > best[1]:
                best[0], best[1] = selection, shortest
            if not hard or shortest >= minimum:
                return selection
            return None

        hit_domain = 0
        use_path = hard and shortest < minimum
        if use_path:
            optimistic, unused_path = navigation(upper)
            if optimistic >= 0 and optimistic < minimum:
                return None
            unused_distance, path = navigation(lower, True)
            path_priority = None
            variable = -1
            for i, domain in enumerate(domains):
                if domain & (domain - 1) == 0:
                    continue
                hits = 0
                bits = domain
                while bits:
                    bit = bits & -bits
                    bits -= bit
                    if blocks[i][bit.bit_length() - 1] & path:
                        hits |= bit
                if not hits:
                    continue
                count, hit_count = domain.bit_count(), hits.bit_count()
                degree = sum(1 for j in neighbors[i] if domains[j].bit_count() > 1)
                score = ((count - hit_count) / count, hit_count, -degree, count)
                if path_priority is None or score < path_priority:
                    path_priority = score
                    variable, hit_domain = i, hits
            if variable < 0:
                return None

        i = variable
        choices = hit_domain if use_path else domains[i]
        ordered = []
        bits = choices
        while bits:
            bit = bits & -bits
            bits -= bit
            a = bit.bit_length() - 1
            d, unused_path = navigation(lower | blocks[i][a])
            if d >= 0:
                support_count = sum((compatible[i][j][a] & domains[j]).bit_count()
                                    for j in neighbors[i])
                ordered.append((-d, -support_count, a, bit))
        ordered.sort()
        for unused_d, unused_s, a, bit in ordered:
            if exhausted() or budget[0] >= limit:
                break
            child = domains[:]
            child[i] = bit
            if propagate(child, deque((j, i) for j in neighbors[i])):
                result = search(child, hard, limit)
                if result is not None:
                    return result
        if use_path and not exhausted() and budget[0] < limit:
            remaining = domains[i] & ~hit_domain
            if remaining:
                child = domains[:]
                child[i] = remaining
                if propagate(child, deque((j, i) for j in neighbors[i])):
                    return search(child, hard, limit)
        return None

    selected = None
    if root_ok:
        selected = search(initial[:], False, 1000)
        if selected is not None and best[1] < minimum:
            selected = None
        if selected is None:
            selected = search(initial[:], True, 32000)
    if selected is None:
        selected = best[0]
    output = list(fixed)
    if selected is not None:
        output.extend(poses[i][a] for i, a in enumerate(selected))
    else:
        for i, name in enumerate(names):
            if poses[i]:
                output.append(poses[i][0])
            elif packet['domains'].get(name):
                output.append(packet['domains'][name][0])
    return {'scene_id': spec['scene_id'], 'objects': output}
