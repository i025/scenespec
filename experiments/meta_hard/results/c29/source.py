def layout(packet):
    from collections import deque

    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    eps = 1e-8
    fixed = list(packet['fixed'])
    fixed_by_id = {o['id']: o for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    regions = {r['id']: r for r in spec.get('regions', [])}
    names = [name for name in required if name not in fixed_by_id]
    n = len(names)
    index = {name: i for i, name in enumerate(names)}

    def rectangle(o):
        asset = assets[o['asset']]
        w, d = asset['width'], asset['depth']
        if o['yaw_deg'] % 180:
            w, d = d, w
        return (o['x'] - w / 2, o['y'] - d / 2,
                o['x'] + w / 2, o['y'] + d / 2)

    def overlap(a, b):
        return (min(a[2], b[2]) - max(a[0], b[0]) > eps and
                min(a[3], b[3]) - max(a[1], b[1]) > eps)

    def front(subject, sr, reference, rr, limit):
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[(reference['yaw_deg'] % 360) // 90]
        dx, dy = subject['x'] - reference['x'], subject['y'] - reference['y']
        hx = (sr[2] - sr[0] + rr[2] - rr[0]) / 2
        hy = (sr[3] - sr[1] + rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * hx - abs(fy) * hy
        lateral = abs(dx * fy - dy * fx)
        return (-eps <= gap <= limit + eps and
                lateral <= abs(fy) * hx + abs(fx) * hy + eps)

    fixed_rects = [rectangle(o) for o in fixed]
    fixed_boxes = {o['id']: r for o, r in zip(fixed, fixed_rects)}
    keep, walls, relations, references = [], {}, [], set()
    for c in spec.get('constraints', []):
        kind = c['type']
        if kind == 'keep_clear':
            r = regions[c['region']]
            keep.append((r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth']))
        elif kind == 'near_wall':
            walls.setdefault(c['subject'], []).append(c)
        elif kind == 'in_front_of':
            relations.append(c)
            references.add(c['reference'])

    gw, gh = int(width), int(depth)
    full = (1 << (gw * gh)) - 1
    left = sum(1 << (y * gw) for y in range(gh))
    right = left << (gw - 1)
    start = 1 << (packet['start'][1] * gw + packet['start'][0])
    goal = 1 << (packet['goal'][1] * gw + packet['goal'][0])
    endpoints = start | goal
    minimum = packet['minimum_steps']

    def cell_mask(r):
        row = 0
        for x in range(gw):
            if r[0] - .15 < x + .5 < r[2] + .15:
                row |= 1 << x
        mask = 0
        if row:
            for y in range(gh):
                if r[1] - .15 < y + .5 < r[3] + .15:
                    mask |= row << (y * gw)
        return mask

    def expand(bits):
        return (((bits & ~right) << 1) | ((bits & ~left) >> 1) |
                (bits << gw) | (bits >> gw)) & full

    nav_cache = {}

    def navigation(blocked):
        cached = nav_cache.get(blocked)
        if cached is not None:
            return cached
        if blocked & endpoints:
            answer = (-1, 0)
        else:
            free = full & ~blocked
            seen, fringe = start, start
            layers = []
            distance = 0
            answer = (-1, 0)
            while fringe:
                if fringe & goal:
                    route = 0
                    if distance < minimum:
                        point = goal
                        route = point
                        for layer in reversed(layers):
                            candidates = expand(point) & layer
                            point = candidates & -candidates
                            route |= point
                    answer = (distance, route)
                    break
                if distance < minimum:
                    layers.append(fringe)
                fringe = expand(fringe) & free & ~seen
                seen |= fringe
                distance += 1
        if len(nav_cache) >= 2048:
            nav_cache.clear()
        nav_cache[blocked] = answer
        return answer

    fixed_blocked = 0
    for r in fixed_rects:
        fixed_blocked |= cell_mask(r)

    poses, rects, masks = [], [], []
    for name in names:
        options, boxes, blocks = [], [], []
        seen = set()
        local_relations = [c for c in relations
                           if c['subject'] == name or c['reference'] == name]
        for o in packet['domains'].get(name, []):
            if o['id'] != name or o['asset'] != required[name]:
                continue
            r = rectangle(o)
            key = r + ((o['yaw_deg'] % 360) if name in references else 0,)
            if key in seen:
                continue
            seen.add(key)
            if r[0] < -eps or r[1] < -eps or r[2] > width + eps or r[3] > depth + eps:
                continue
            if any(overlap(r, z) for z in keep) or any(overlap(r, z) for z in fixed_rects):
                continue
            valid = True
            for c in walls.get(name, []):
                gap = {'north': depth - r[3], 'south': r[1],
                       'east': width - r[2], 'west': r[0]}[c['wall']]
                if not -eps <= gap <= c['max_gap'] + eps:
                    valid = False
                    break
            if not valid:
                continue
            for c in local_relations:
                subject, reference = c['subject'], c['reference']
                if subject == name and reference in fixed_by_id:
                    valid = front(o, r, fixed_by_id[reference], fixed_boxes[reference], c['max_gap'])
                elif reference == name and subject in fixed_by_id:
                    valid = front(fixed_by_id[subject], fixed_boxes[subject], o, r, c['max_gap'])
                elif subject == name and reference == name:
                    valid = front(o, r, o, r, c['max_gap'])
                if not valid:
                    break
            if not valid:
                continue
            block = cell_mask(r)
            if block & endpoints:
                continue
            options.append(o)
            boxes.append(r)
            blocks.append(block)
        poses.append(options)
        rects.append(boxes)
        masks.append(blocks)

    pair_relations = {}
    related = [set() for _ in names]
    for c in relations:
        if c['subject'] in index and c['reference'] in index:
            i, j = index[c['subject']], index[c['reference']]
            if i != j:
                pair_relations.setdefault((min(i, j), max(i, j)), []).append(c)
                related[i].add(j)
                related[j].add(i)

    compatible = [[None] * n for _ in names]
    for i in range(n):
        for j in range(i + 1, n):
            forward = [0] * len(poses[i])
            backward = [0] * len(poses[j])
            tests = pair_relations.get((i, j), [])
            for a, oa in enumerate(poses[i]):
                ra = rects[i][a]
                for b, ob in enumerate(poses[j]):
                    rb = rects[j][b]
                    if overlap(ra, rb):
                        continue
                    valid = True
                    for c in tests:
                        if c['subject'] == names[i]:
                            valid = front(oa, ra, ob, rb, c['max_gap'])
                        else:
                            valid = front(ob, rb, oa, ra, c['max_gap'])
                        if not valid:
                            break
                    if valid:
                        forward[a] |= 1 << b
                        backward[b] |= 1 << a
            compatible[i][j] = forward
            compatible[j][i] = backward

    def propagate(ds, seeds):
        queue = deque(seeds)
        queued = set(queue)
        while queue:
            i = queue.popleft()
            queued.discard(i)
            di = ds[i]
            if not di:
                return False
            singleton = not di & (di - 1)
            if singleton:
                a = di.bit_length() - 1
                targets = range(n)
            else:
                targets = related[i]
            for j in targets:
                if i == j:
                    continue
                old = ds[j]
                if singleton:
                    revised = old & compatible[i][j][a]
                else:
                    remaining, revised = old, 0
                    table = compatible[j][i]
                    while remaining:
                        bit = remaining & -remaining
                        if table[bit.bit_length() - 1] & di:
                            revised |= bit
                        remaining -= bit
                if not revised:
                    return False
                if revised != old:
                    ds[j] = revised
                    if j not in queued:
                        queue.append(j)
                        queued.add(j)
        return True

    def accessible(objects, boxes):
        by_id = {o['id']: k for k, o in enumerate(objects)}
        for access in spec.get('access_paths', []):
            clearance = access.get('clearance', 0)
            interaction = access.get('interaction_distance', 0)
            region = regions[access['from_region']]
            target = boxes[by_id[access['to_object']]]
            expanded = [(r[0] - clearance, r[1] - clearance,
                         r[2] + clearance, r[3] + clearance) for r in boxes]
            xs = {0.0, float(width), region['x'], region['x'] + region['width']}
            ys = {0.0, float(depth), region['y'], region['y'] + region['depth']}
            for r in expanded:
                xs.add(max(0.0, min(width, r[0])))
                xs.add(max(0.0, min(width, r[2])))
                ys.add(max(0.0, min(depth, r[1])))
                ys.add(max(0.0, min(depth, r[3])))
            xs, ys = sorted(xs), sorted(ys)
            nx, ny = len(xs) - 1, len(ys) - 1
            free = [True] * (nx * ny)
            seeds, goals = [], set()
            for y in range(ny):
                cy = (ys[y] + ys[y + 1]) / 2
                for x in range(nx):
                    cx = (xs[x] + xs[x + 1]) / 2
                    k = y * nx + x
                    if any(r[0] < cx < r[2] and r[1] < cy < r[3] for r in expanded):
                        free[k] = False
                        continue
                    if (region['x'] <= cx <= region['x'] + region['width'] and
                            region['y'] <= cy <= region['y'] + region['depth']):
                        seeds.append(k)
                    dx = max(target[0] - xs[x + 1], xs[x] - target[2], 0)
                    dy = max(target[1] - ys[y + 1], ys[y] - target[3], 0)
                    if dx * dx + dy * dy < interaction * interaction + eps:
                        goals.add(k)
            queue = deque(seeds)
            visited = set(seeds)
            reached = False
            while queue:
                k = queue.popleft()
                if k in goals:
                    reached = True
                    break
                x, y = k % nx, k // nx
                neighbors = []
                if x:
                    neighbors.append(k - 1)
                if x + 1 < nx:
                    neighbors.append(k + 1)
                if y:
                    neighbors.append(k - nx)
                if y + 1 < ny:
                    neighbors.append(k + nx)
                for q in neighbors:
                    if free[q] and q not in visited:
                        visited.add(q)
                        queue.append(q)
            if not reached:
                return False
        return True

    result = [None]
    fallback = [None, -1]
    budget = [0]

    def search(ds):
        while True:
            budget[0] += 1
            if budget[0] > 40000:
                return False
            blocked = fixed_blocked
            possible = fixed_blocked
            chosen, unions = [], []
            variable, best_key = -1, None
            for i, domain in enumerate(ds):
                if not domain:
                    return False
                count = domain.bit_count()
                if count == 1:
                    a = domain.bit_length() - 1
                    m = masks[i][a]
                    blocked |= m
                    possible |= m
                    chosen.append(a)
                    unions.append(m)
                else:
                    chosen.append(-1)
                    common, union = full, 0
                    remaining = domain
                    while remaining:
                        bit = remaining & -remaining
                        m = masks[i][bit.bit_length() - 1]
                        common &= m
                        union |= m
                        remaining -= bit
                    blocked |= common
                    possible |= union
                    unions.append(union)
                    key = (count, -len(related[i]))
                    if best_key is None or key < best_key:
                        best_key, variable = key, i
            distance, path = navigation(blocked)
            if distance < 0:
                return False
            if variable < 0:
                objects = fixed + [poses[i][a] for i, a in enumerate(chosen)]
                if distance >= minimum or distance > fallback[1]:
                    boxes = fixed_rects + [rects[i][a] for i, a in enumerate(chosen)]
                    if accessible(objects, boxes):
                        if distance >= minimum:
                            result[0] = objects
                            return True
                        fallback[0], fallback[1] = objects, distance
                return False
            if distance >= minimum:
                break

            # Every completion must hit this particular too-short route.
            # A sole supporting object must choose one of its hitting poses.
            supporter, multiple = -1, False
            for i in range(n):
                if chosen[i] < 0 and unions[i] & path:
                    if supporter >= 0:
                        multiple = True
                        break
                    supporter = i
            if supporter < 0:
                return False
            if not multiple:
                remaining = ds[supporter]
                hitting = 0
                while remaining:
                    bit = remaining & -remaining
                    if masks[supporter][bit.bit_length() - 1] & path:
                        hitting |= bit
                    remaining -= bit
                if hitting != ds[supporter]:
                    ds[supporter] = hitting
                    if not propagate(ds, [supporter]):
                        return False
                    continue
            optimistic, unused = navigation(possible)
            if 0 <= optimistic < minimum:
                return False
            break

        options = []
        remaining = ds[variable]
        while remaining:
            bit = remaining & -remaining
            a = bit.bit_length() - 1
            impact = (masks[variable][a] & path).bit_count()
            support = 0
            for j in range(n):
                if j != variable and ds[j] & (ds[j] - 1):
                    support += (compatible[variable][j][a] & ds[j]).bit_count()
            options.append((-impact, -support, a))
            remaining -= bit
        options.sort()
        for unused, unused2, a in options:
            child = ds[:]
            child[variable] = 1 << a
            if propagate(child, [variable]) and search(child):
                return True
            if budget[0] > 40000:
                break
        return False

    initial = [(1 << len(options)) - 1 for options in poses]
    if all(initial) and propagate(initial, range(n)):
        search(initial)
    if result[0] is not None:
        output = result[0]
    elif fallback[0] is not None:
        output = fallback[0]
    else:
        output = fixed[:]
        for i, name in enumerate(names):
            if poses[i]:
                output.append(poses[i][0])
            else:
                supplied = packet['domains'].get(name, [])
                if supplied:
                    output.append(supplied[0])
    return {'scene_id': spec['scene_id'], 'objects': output}
