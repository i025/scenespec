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
        yaw = reference['yaw_deg'] % 360
        fx, fy = {0: (0, 1), 90: (-1, 0), 180: (0, -1), 270: (1, 0)}[yaw]
        dx = subject['x'] - reference['x']
        dy = subject['y'] - reference['y']
        sx, sy = (sr[2] - sr[0]) / 2, (sr[3] - sr[1]) / 2
        rx, ry = (rr[2] - rr[0]) / 2, (rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * (sx + rx) - abs(fy) * (sy + ry)
        lateral = abs(dx * fy - dy * fx)
        return (-1e-8 <= gap <= limit + 1e-8 and
                lateral <= abs(fy) * (sx + rx) + abs(fx) * (sy + ry) + 1e-8)

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
    endpoints = (1 << start) | (1 << goal)
    left_edge = sum(1 << (y * nx) for y in range(ny))
    right_edge = left_edge << (nx - 1)
    move_right = all_cells ^ right_edge
    move_left = all_cells ^ left_edge
    minimum = packet['minimum_steps']

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

    def distance(blocked):
        if blocked & endpoints:
            return -1
        free = all_cells & ~blocked
        frontier = 1 << start
        seen = frontier
        target = 1 << goal
        steps = 0
        while frontier:
            if frontier & target:
                return steps
            frontier = (((frontier & move_right) << 1) |
                        ((frontier & move_left) >> 1) |
                        (frontier << nx) | (frontier >> nx)) & free & ~seen
            seen |= frontier
            steps += 1
        return -1

    fixed_rects = [rectangle(o) for o in fixed]
    fixed_blocked = 0
    for r in fixed_rects:
        fixed_blocked |= raster(r, 0.15, nx, ny, 1.0)

    names = sorted(n for n in required if n not in fixed_by_id)
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
            if any(overlap(r, z) for z in forbidden):
                continue
            if any(overlap(r, z) for z in fixed_rects):
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

    budget = [0, 0]

    def propagate(domains, queue):
        while queue:
            i, j = queue.popleft()
            budget[1] += 1
            if budget[1] > 900000:
                return False
            remaining = domains[i]
            revised = 0
            support = compatible[i][j]
            while remaining:
                bit = remaining & -remaining
                remaining -= bit
                a = bit.bit_length() - 1
                if support[a] & domains[j]:
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
    ax, ay = max(1, int(math.ceil(width / step))), max(1, int(math.ceil(depth / step)))
    access_all = (1 << (ax * ay)) - 1
    access_left = sum(1 << (y * ax) for y in range(ay))
    access_right = access_left << (ax - 1)
    access_rm = access_all ^ access_right
    access_lm = access_all ^ access_left
    access_masks = {}
    access_sources = {}
    approach_masks = {}

    def accessible(selection):
        objects = dict(fixed_by_id)
        boxes = {o['id']: r for o, r in zip(fixed, fixed_rects)}
        for i, a in enumerate(selection):
            objects[names[i]] = poses[i][a]
            boxes[names[i]] = rects[i][a]
        floods = {}
        for path in access:
            clearance = path.get('clearance', 0.15)
            region_id = path['from_region']
            flood_key = (clearance, region_id)
            if flood_key not in floods:
                blocked = 0
                for name, r in boxes.items():
                    key = (r, clearance)
                    if key not in access_masks:
                        access_masks[key] = raster(r, clearance, ax, ay, step)
                    blocked |= access_masks[key]
                free = access_all & ~blocked
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
    answers = [None]
    leaf_cache = {}

    def search(domains, require_minimum, node_limit):
        budget[0] += 1
        if budget[0] > node_limit or budget[1] > 900000:
            return None
        lower = fixed_blocked
        upper = fixed_blocked
        variable = -1
        priority = None
        for i, domain in enumerate(domains):
            common = all_cells
            union = 0
            bits = domain
            while bits:
                bit = bits & -bits
                bits -= bit
                m = blocks[i][bit.bit_length() - 1]
                common &= m
                union |= m
            lower |= common
            upper |= union
            count = domain.bit_count()
            if count > 1:
                degree = sum(1 for j in neighbors[i] if domains[j].bit_count() > 1)
                score = (count, -degree)
                if priority is None or score < priority:
                    priority, variable = score, i
        shortest = distance(lower)
        if shortest < 0:
            return None
        if require_minimum and shortest < minimum:
            optimistic = distance(upper)
            if optimistic >= 0 and optimistic < minimum:
                return None
        if variable < 0:
            selection = [d.bit_length() - 1 for d in domains]
            key = tuple(selection)
            good = leaf_cache.get(key)
            if good is None:
                good = accessible(selection)
                if len(leaf_cache) < 12000:
                    leaf_cache[key] = good
            if not good:
                return None
            if answers[0] is None:
                answers[0] = selection
            if not require_minimum or shortest >= minimum:
                return selection
            return None
        i = variable
        ordered = []
        bits = domains[i]
        while bits:
            bit = bits & -bits
            bits -= bit
            a = bit.bit_length() - 1
            d = distance(lower | blocks[i][a])
            if d >= 0:
                support_count = sum((compatible[i][j][a] & domains[j]).bit_count()
                                    for j in neighbors[i])
                ordered.append((-d, -support_count, a, bit))
        ordered.sort()
        for unused_d, unused_s, a, bit in ordered:
            if budget[0] >= node_limit or budget[1] > 900000:
                break
            child = domains[:]
            child[i] = bit
            queue = deque((j, i) for j in neighbors[i])
            if propagate(child, queue):
                result = search(child, require_minimum, node_limit)
                if result is not None:
                    return result
        return None

    selected = None
    if root_ok:
        selected = search(initial[:], False, 3500)
        if selected is not None:
            mask = fixed_blocked
            for i, a in enumerate(selected):
                mask |= blocks[i][a]
            if distance(mask) < minimum:
                selected = None
        if selected is None:
            selected = search(initial[:], True, 32000)
    if selected is None:
        selected = answers[0]
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
