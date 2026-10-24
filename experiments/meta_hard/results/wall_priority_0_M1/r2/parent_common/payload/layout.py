def layout(packet):
    from collections import deque
    from math import ceil, sqrt

    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    eps = 1e-8
    fixed = list(packet['fixed'])
    fixed_by_id = {o['id']: o for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    constraints = spec.get('constraints', [])
    regions = {r['id']: r for r in spec.get('regions', [])}
    names = [n for n in required if n not in fixed_by_id]
    n = len(names)
    index = {name: i for i, name in enumerate(names)}

    def rectangle(o):
        a = assets[o['asset']]
        w, d = a['width'], a['depth']
        if o['yaw_deg'] % 180:
            w, d = d, w
        return (o['x'] - w / 2, o['y'] - d / 2,
                o['x'] + w / 2, o['y'] + d / 2)

    def overlap(a, b):
        return (min(a[2], b[2]) - max(a[0], b[0]) > eps and
                min(a[3], b[3]) - max(a[1], b[1]) > eps)

    def front(subject, sr, reference, rr, gap_limit):
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[(reference['yaw_deg'] % 360) // 90]
        dx = subject['x'] - reference['x']
        dy = subject['y'] - reference['y']
        hx = (sr[2] - sr[0] + rr[2] - rr[0]) / 2
        hy = (sr[3] - sr[1] + rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * hx - abs(fy) * hy
        lateral = abs(dx * fy - dy * fx)
        return -eps <= gap <= gap_limit + eps and lateral <= abs(fy) * hx + abs(fx) * hy + eps

    fixed_rects = [rectangle(o) for o in fixed]
    keep = []
    walls = {}
    relations = []
    references = set()
    for c in constraints:
        kind = c['type']
        if kind == 'keep_clear':
            z = regions[c['region']]
            keep.append((z['x'], z['y'], z['x'] + z['width'], z['y'] + z['depth']))
        elif kind == 'near_wall':
            walls.setdefault(c['subject'], []).append(c)
        elif kind == 'in_front_of':
            relations.append(c)
            references.add(c['reference'])

    gw, gh = int(width), int(depth)
    cells = gw * gh
    full = (1 << cells) - 1
    left = sum(1 << (y * gw) for y in range(gh))
    right = left << (gw - 1)
    start = 1 << (packet['start'][1] * gw + packet['start'][0])
    goal = 1 << (packet['goal'][1] * gw + packet['goal'][0])
    endpoints = start | goal
    minimum = packet['minimum_steps']

    def cell_mask(r):
        result = 0
        for y in range(gh):
            if r[1] - .15 < y + .5 < r[3] + .15:
                for x in range(gw):
                    if r[0] - .15 < x + .5 < r[2] + .15:
                        result |= 1 << (y * gw + x)
        return result

    def expand(bits):
        return (((bits & ~right) << 1) | ((bits & ~left) >> 1) |
                (bits << gw) | (bits >> gw)) & full

    def navigation(blocked, want_path=False):
        if blocked & endpoints:
            return -1, 0
        free = full & ~blocked
        seen = start
        fringe = start
        layers = [start] if want_path else []
        distance = 0
        while fringe:
            if fringe & goal:
                if not want_path or distance >= minimum:
                    return distance, 0
                route = goal
                point = goal
                for layer in reversed(layers[:-1]):
                    candidates = expand(point) & layer
                    point = candidates & -candidates
                    route |= point
                return distance, route
            fringe = expand(fringe) & free & ~seen
            seen |= fringe
            distance += 1
            if want_path:
                layers.append(fringe)
        return -1, 0

    fixed_blocked = 0
    for r in fixed_rects:
        fixed_blocked |= cell_mask(r)

    poses, rects, masks = [], [], []
    for name in names:
        options, boxes, blocks = [], [], []
        seen = set()
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
                gap = {'north': depth-r[3], 'south': r[1],
                       'east': width-r[2], 'west': r[0]}[c['wall']]
                if not -eps <= gap <= c['max_gap'] + eps:
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
                if c['subject'] == name and c['reference'] == name:
                    if not front(o, r, o, r, c['max_gap']):
                        valid = False
                        break
            block = cell_mask(r)
            if valid and not block & endpoints:
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

    compatible = [[None for _ in names] for _ in names]
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
        queued = set(seeds)
        while queue:
            i = queue.popleft()
            queued.discard(i)
            di = ds[i]
            if not di:
                return False
            singleton = not di & (di - 1)
            targets = range(n) if singleton else related[i]
            for j in targets:
                if i == j:
                    continue
                old = ds[j]
                remaining = old
                revised = 0
                table = compatible[j][i]
                while remaining:
                    bit = remaining & -remaining
                    a = bit.bit_length() - 1
                    if table[a] & di:
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

    # Access checks use continuous rectilinear free-space decomposition.
    # Midpoints represent open arrangement cells; shared edges connect cells.
    def accessible(objects, boxes):
        by_id = {o['id']: k for k, o in enumerate(objects)}
        for access in spec.get('access_paths', []):
            clearance = access.get('clearance', 0)
            interaction = access.get('interaction_distance', 0)
            region = regions[access['from_region']]
            target = boxes[by_id[access['to_object']]]
            expanded = [(r[0]-clearance, r[1]-clearance,
                         r[2]+clearance, r[3]+clearance) for r in boxes]
            xs = {0.0, float(width), region['x'], region['x']+region['width']}
            ys = {0.0, float(depth), region['y'], region['y']+region['depth']}
            for r in expanded:
                xs.add(max(0.0, min(width, r[0])))
                xs.add(max(0.0, min(width, r[2])))
                ys.add(max(0.0, min(depth, r[1])))
                ys.add(max(0.0, min(depth, r[3])))
            xs, ys = sorted(xs), sorted(ys)
            nx, ny = len(xs)-1, len(ys)-1
            free = [True] * (nx * ny)
            seeds = []
            goals = set()
            for y in range(ny):
                cy = (ys[y] + ys[y+1]) / 2
                for x in range(nx):
                    cx = (xs[x] + xs[x+1]) / 2
                    k = y * nx + x
                    if any(r[0] < cx < r[2] and r[1] < cy < r[3] for r in expanded):
                        free[k] = False
                        continue
                    if (region['x'] <= cx <= region['x'] + region['width'] and
                            region['y'] <= cy <= region['y'] + region['depth']):
                        seeds.append(k)
                    dx = max(target[0]-xs[x+1], xs[x]-target[2], 0)
                    dy = max(target[1]-ys[y+1], ys[y]-target[3], 0)
                    if dx*dx + dy*dy < interaction*interaction + eps:
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
                    neighbors.append(k-1)
                if x+1 < nx:
                    neighbors.append(k+1)
                if y:
                    neighbors.append(k-nx)
                if y+1 < ny:
                    neighbors.append(k+nx)
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
        budget[0] += 1
        if budget[0] > 40000:
            return False
        blocked = fixed_blocked
        possible = fixed_blocked
        chosen = []
        variable = -1
        best_key = None
        for i, domain in enumerate(ds):
            if not domain:
                return False
            count = domain.bit_count()
            if count == 1:
                a = domain.bit_length()-1
                blocked |= masks[i][a]
                possible |= masks[i][a]
                chosen.append(a)
            else:
                chosen.append(-1)
                common = full
                union = 0
                remaining = domain
                while remaining:
                    bit = remaining & -remaining
                    m = masks[i][bit.bit_length()-1]
                    common &= m
                    union |= m
                    remaining -= bit
                blocked |= common
                possible |= union
                key = (count, -len(related[i]))
                if best_key is None or key < best_key:
                    best_key, variable = key, i
        distance, path = navigation(blocked, True)
        if distance < 0:
            return False
        if variable < 0:
            objects = fixed + [poses[i][a] for i, a in enumerate(chosen)]
            boxes = fixed_rects + [rects[i][a] for i, a in enumerate(chosen)]
            if distance >= minimum:
                if accessible(objects, boxes):
                    result[0] = objects
                    return True
            if distance > fallback[1] and accessible(objects, boxes):
                fallback[0], fallback[1] = objects, distance
            return False
        if distance < minimum:
            optimistic, unused = navigation(possible)
            if 0 <= optimistic < minimum:
                return False
        options = []
        remaining = ds[variable]
        while remaining:
            bit = remaining & -remaining
            a = bit.bit_length()-1
            impact = (masks[variable][a] & path).bit_count()
            support = 0
            for j in range(n):
                if j != variable and ds[j] & (ds[j]-1):
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

    initial = [(1 << len(options))-1 for options in poses]
    if all(initial) and propagate(initial, range(n)):
        search(initial)
    if result[0] is not None:
        output = result[0]
    elif fallback[0] is not None:
        output = fallback[0]
    else:
        output = fixed[:]
        for name in names:
            supplied = packet['domains'].get(name, [])
            if supplied:
                output.append(supplied[0])
    return {'scene_id': spec['scene_id'], 'objects': output}
