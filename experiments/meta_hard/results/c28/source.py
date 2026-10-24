from collections import deque

def layout(packet):
    spec = packet['spec']
    width, depth = packet['width'], packet['depth']
    assets = spec['assets']
    fixed = list(packet['fixed'])
    fixed_ids = {o['id'] for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    names = [o['id'] for o in spec['required_objects'] if o['id'] not in fixed_ids]
    regions = {r['id']: r for r in spec.get('regions', [])}
    walls, relations = {}, {}
    references = set()
    clear_regions = []
    for c in spec.get('constraints', []):
        kind = c['type']
        if kind == 'near_wall':
            walls.setdefault(c['subject'], []).append(c)
        elif kind == 'in_front_of':
            relations.setdefault((c['subject'], c['reference']), []).append(c)
            references.add(c['reference'])
        elif kind == 'keep_clear':
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

    def front(c, a, ra, b, rb):
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[int(b['yaw_deg'] % 360) // 90]
        if fx:
            gap = (a['x'] - b['x']) * fx - (ra[2] - ra[0] + rb[2] - rb[0]) / 2
            lateral = abs(a['y'] - b['y'])
            limit = (ra[3] - ra[1] + rb[3] - rb[1]) / 2
        else:
            gap = (a['y'] - b['y']) * fy - (ra[3] - ra[1] + rb[3] - rb[1]) / 2
            lateral = abs(a['x'] - b['x'])
            limit = (ra[2] - ra[0] + rb[2] - rb[0]) / 2
        return -1e-8 <= gap <= c['max_gap'] + 1e-8 and lateral <= limit + 1e-8

    def compatible(a, ra, b, rb):
        if overlap(ra, rb):
            return False
        for c in relations.get((a['id'], b['id']), ()):
            if not front(c, a, ra, b, rb):
                return False
        for c in relations.get((b['id'], a['id']), ()):
            if not front(c, b, rb, a, ra):
                return False
        return True

    gw, gh = int(width), int(depth)
    whole = (1 << (gw * gh)) - 1
    left = sum(1 << (y * gw) for y in range(gh))
    not_left = whole ^ left
    not_right = whole ^ (left << (gw - 1))
    start = int(packet['start'][1]) * gw + int(packet['start'][0])
    goal = int(packet['goal'][1]) * gw + int(packet['goal'][0])
    start_bit, goal_bit = 1 << start, 1 << goal
    endpoints = start_bit | goal_bit
    minimum = packet['minimum_steps']
    effort = [0, 0]
    nav_cache = {}

    def blocked_cells(r):
        mask = 0
        for y in range(gh):
            if r[1] - .15 < y + .5 < r[3] + .15:
                for x in range(gw):
                    if r[0] - .15 < x + .5 < r[2] + .15:
                        mask |= 1 << (y * gw + x)
        return mask

    def expand(bits):
        return ((bits & not_right) << 1) | ((bits & not_left) >> 1) | (bits << gw) | (bits >> gw)

    def shortest(blocked):
        cached = nav_cache.get(blocked)
        if cached is not None:
            return cached
        if blocked & endpoints:
            return (-1, 0)
        free = whole & ~blocked
        wave = start_bit
        seen = wave
        layers = [wave]
        while not wave & goal_bit:
            effort[1] += 1
            wave = expand(wave) & free & ~seen
            if not wave:
                result = (-1, 0)
                if len(nav_cache) < 16000:
                    nav_cache[blocked] = result
                return result
            seen |= wave
            layers.append(wave)
        cell = goal_bit
        path = cell
        for layer in reversed(layers[:-1]):
            options = expand(cell) & layer
            cell = options & -options
            path |= cell
        result = (len(layers) - 1, path)
        if len(nav_cache) < 16000:
            nav_cache[blocked] = result
        return result

    fixed_rects = [rectangle(o) for o in fixed]
    fixed_blocked = 0
    for r in fixed_rects:
        fixed_blocked |= blocked_cells(r)
    poses, rects, blocks = [], [], []
    for name in names:
        pp, rr, bb = [], [], []
        seen = set()
        for o in packet['domains'].get(name, []):
            if o['id'] != name or o['asset'] != required[name]:
                continue
            r = rectangle(o)
            key = r + ((o['yaw_deg'] % 360,) if name in references else ())
            if key in seen:
                continue
            seen.add(key)
            if r[0] < -1e-8 or r[1] < -1e-8 or r[2] > width + 1e-8 or r[3] > depth + 1e-8:
                continue
            if any(overlap(r, z) for z in clear_regions):
                continue
            valid = True
            for c in walls.get(name, ()):
                gap = {'west': r[0], 'east': width - r[2], 'south': r[1], 'north': depth - r[3]}[c['wall']]
                if not -1e-8 <= gap <= c['max_gap'] + 1e-8:
                    valid = False
                    break
            if not valid:
                continue
            if any(not compatible(o, r, f, fr) for f, fr in zip(fixed, fixed_rects)):
                continue
            b = blocked_cells(r)
            if b & endpoints:
                continue
            pp.append(o)
            rr.append(r)
            bb.append(b)
        poses.append(pp)
        rects.append(rr)
        blocks.append(bb)

    n = len(names)
    supports = [[None] * n for i in range(n)]
    neighbors = [[] for i in range(n)]
    for i in range(n):
        for j in range(i):
            ij = [0] * len(poses[i])
            ji = [0] * len(poses[j])
            full_i = (1 << len(poses[i])) - 1
            full_j = (1 << len(poses[j])) - 1
            for a in range(len(poses[i])):
                for b in range(len(poses[j])):
                    if compatible(poses[i][a], rects[i][a], poses[j][b], rects[j][b]):
                        ij[a] |= 1 << b
                        ji[b] |= 1 << a
            if any(v != full_j for v in ij) or any(v != full_i for v in ji):
                supports[i][j] = ij
                supports[j][i] = ji
                neighbors[i].append(j)
                neighbors[j].append(i)

    def propagate(domains, changed):
        if any(d == 0 for d in domains):
            return False
        if changed is None:
            queue = deque((i, j) for i in range(n) for j in neighbors[i])
        else:
            queue = deque((i, j) for j in changed for i in neighbors[j])
        pending = set(queue)
        while queue:
            i, j = queue.popleft()
            pending.discard((i, j))
            old, other = domains[i], domains[j]
            values, new = old, 0
            table = supports[i][j]
            while values:
                bit = values & -values
                values -= bit
                effort[1] += 1
                if table[bit.bit_length() - 1] & other:
                    new |= bit
            if new != old:
                if not new:
                    return False
                domains[i] = new
                for k in neighbors[i]:
                    if k != j and (k, i) not in pending:
                        queue.append((k, i))
                        pending.add((k, i))
        return True

    def accessible(objects, all_rects):
        by_id = {o['id']: i for i, o in enumerate(objects)}
        for request in spec.get('access_paths', []):
            clearance = request.get('clearance', .15)
            interaction = request.get('interaction_distance', .8)
            region = regions[request['from_region']]
            target = all_rects[by_id[request['to_object']]]
            xmin, xmax = clearance, width - clearance
            ymin, ymax = clearance, depth - clearance
            if xmin >= xmax or ymin >= ymax:
                return False
            expanded = [(r[0] - clearance, r[1] - clearance, r[2] + clearance, r[3] + clearance) for r in all_rects]
            xs, ys = {xmin, xmax}, {ymin, ymax}
            for r in expanded:
                for x in (r[0], r[2]):
                    if xmin < x < xmax:
                        xs.add(x)
                for y in (r[1], r[3]):
                    if ymin < y < ymax:
                        ys.add(y)
            xs, ys = sorted(xs), sorted(ys)
            nx, ny = len(xs) - 1, len(ys) - 1
            free = [False] * (nx * ny)
            destinations = [False] * (nx * ny)
            queue = deque()
            visited = set()
            for y in range(ny):
                cy = (ys[y] + ys[y + 1]) / 2
                for x in range(nx):
                    cx = (xs[x] + xs[x + 1]) / 2
                    k = y * nx + x
                    if any(r[0] < cx < r[2] and r[1] < cy < r[3] for r in expanded):
                        continue
                    free[k] = True
                    dx = max(target[0] - xs[x + 1], xs[x] - target[2], 0)
                    dy = max(target[1] - ys[y + 1], ys[y] - target[3], 0)
                    destinations[k] = dx * dx + dy * dy < interaction * interaction - 1e-10
                    if min(xs[x + 1], region['x'] + region['width']) > max(xs[x], region['x']) + 1e-9 and min(ys[y + 1], region['y'] + region['depth']) > max(ys[y], region['y']) + 1e-9:
                        queue.append(k)
                        visited.add(k)
            reached = False
            while queue:
                k = queue.popleft()
                if destinations[k]:
                    reached = True
                    break
                x, y = k % nx, k // nx
                adjacent = []
                if x:
                    adjacent.append(k - 1)
                if x + 1 < nx:
                    adjacent.append(k + 1)
                if y:
                    adjacent.append(k - nx)
                if y + 1 < ny:
                    adjacent.append(k + nx)
                for q in adjacent:
                    if free[q] and q not in visited:
                        visited.add(q)
                        queue.append(q)
            if not reached:
                return False
        return True

    dead = set()
    fallback = [None, -1]

    def exhausted():
        return effort[0] >= 35000 or effort[1] >= 2400000

    def remember(key):
        if len(dead) < 30000:
            dead.add(key)

    def search(domains, changed):
        if exhausted():
            return None
        effort[0] += 1
        if not propagate(domains, changed):
            return None
        while True:
            key = tuple(domains)
            if key in dead:
                return None
            forced, possible = fixed_blocked, fixed_blocked
            undecided = []
            for i, d in enumerate(domains):
                values, common, union = d, whole, 0
                while values:
                    bit = values & -values
                    values -= bit
                    b = blocks[i][bit.bit_length() - 1]
                    common &= b
                    union |= b
                    effort[1] += 1
                forced |= common
                possible |= union
                if d & (d - 1):
                    undecided.append(i)
            distance, path = shortest(forced)
            if distance < 0:
                remember(key)
                return None
            if not undecided:
                indices = [d.bit_length() - 1 for d in domains]
                objects = fixed + [poses[i][indices[i]] for i in range(n)]
                if distance >= minimum or distance > fallback[1]:
                    all_rects = fixed_rects + [rects[i][indices[i]] for i in range(n)]
                    if accessible(objects, all_rects):
                        if distance >= minimum:
                            return objects
                        fallback[0], fallback[1] = objects, distance
                remember(key)
                return None
            cutters = []
            if distance < minimum:
                upper, unused = shortest(possible)
                if 0 <= upper < minimum:
                    remember(key)
                    return None
                for i in undecided:
                    values, cutting = domains[i], 0
                    while values:
                        bit = values & -values
                        values -= bit
                        if blocks[i][bit.bit_length() - 1] & path:
                            cutting |= bit
                    if cutting:
                        cutters.append((i, cutting))
                if not cutters:
                    remember(key)
                    return None
                if len(cutters) == 1:
                    i, cutting = cutters[0]
                    if cutting != domains[i]:
                        domains[i] = cutting
                        if not propagate(domains, [i]):
                            return None
                        continue
            break
        if exhausted():
            return None
        cutting_by_id = {i: d for i, d in cutters}
        i = min(undecided, key=lambda j: (domains[j].bit_count(), -len(neighbors[j]), -int(j in cutting_by_id)))
        if distance < minimum and len(cutters) > 1 and domains[i].bit_count() > 2:
            ordered = []
            for j, cutting in cutters:
                best_distance, best_hit = -1, 0
                values = cutting
                while values:
                    bit = values & -values
                    values -= bit
                    b = blocks[j][bit.bit_length() - 1]
                    d, unused = shortest(forced | b)
                    if d >= 0:
                        best_distance = max(best_distance, min(d, minimum))
                        best_hit = max(best_hit, (b & path).bit_count())
                ordered.append((best_distance, best_hit, -cutting.bit_count(), j, cutting))
            ordered.sort(reverse=True)
            remaining = list(domains)
            prior_changes = []
            for score, hit, size, j, cutting in ordered:
                child = list(remaining)
                child[j] &= cutting
                if child[j]:
                    result = search(child, prior_changes + [j])
                    if result is not None:
                        return result
                if exhausted():
                    return None
                remaining[j] &= ~cutting
                prior_changes.append(j)
                if not remaining[j]:
                    break
            remember(key)
            return None
        choices = []
        values = domains[i]
        while values:
            bit = values & -values
            values -= bit
            a = bit.bit_length() - 1
            d, unused = shortest(forced | blocks[i][a])
            if d < 0:
                continue
            freedom = 0
            for j in neighbors[i]:
                if domains[j] & (domains[j] - 1):
                    freedom += (supports[i][j][a] & domains[j]).bit_count()
            hit = (blocks[i][a] & path).bit_count() if distance < minimum else 0
            o = poses[i][a]
            choices.append((min(d, minimum), hit, freedom, o['x'], o['y'], o['yaw_deg'], bit))
        choices.sort(reverse=True)
        for choice in choices:
            child = list(domains)
            child[i] = choice[-1]
            result = search(child, [i])
            if result is not None:
                return result
            if exhausted():
                return None
        remember(key)
        return None

    initial = [(1 << len(p)) - 1 for p in poses]
    result = search(initial, None)
    if result is None:
        result = fallback[0]
    if result is None:
        result = fixed + [poses[i][0] if poses[i] else packet['domains'][names[i]][0] for i in range(n)]
    return {'scene_id': spec['scene_id'], 'objects': result}
