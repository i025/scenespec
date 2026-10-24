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
    constraints = spec.get('constraints', [])
    relations = {}
    references = set()
    walls = {}
    clear_regions = []
    for c in constraints:
        t = c['type']
        if t == 'in_front_of':
            relations.setdefault((c['subject'], c['reference']), []).append(c)
            references.add(c['reference'])
        elif t == 'near_wall':
            walls.setdefault(c['subject'], []).append(c)
        elif t == 'keep_clear':
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
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[(b['yaw_deg'] % 360) // 90]
        dx, dy = a['x'] - b['x'], a['y'] - b['y']
        if fx:
            gap = dx * fx - (ra[2] - ra[0] + rb[2] - rb[0]) / 2
            lateral = abs(dy)
            limit = (ra[3] - ra[1] + rb[3] - rb[1]) / 2
        else:
            gap = dy * fy - (ra[3] - ra[1] + rb[3] - rb[1]) / 2
            lateral = abs(dx)
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
    count = gw * gh
    whole = (1 << count) - 1
    left = sum(1 << (y * gw) for y in range(gh))
    right = left << (gw - 1)
    not_left, not_right = whole ^ left, whole ^ right
    start = int(packet['start'][1]) * gw + int(packet['start'][0])
    goal = int(packet['goal'][1]) * gw + int(packet['goal'][0])
    start_bit, goal_bit = 1 << start, 1 << goal
    endpoints = start_bit | goal_bit
    minimum = packet['minimum_steps']

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

    bfs_cache = {}

    def shortest(blocked, need_path=False):
        cached = bfs_cache.get(blocked)
        if cached is not None and (not need_path or cached[0] < 0 or cached[1]):
            return cached
        if blocked & endpoints:
            return -1, 0
        free = whole & ~blocked
        seen = start_bit
        wave = start_bit
        layers = [wave] if need_path else []
        distance = 0
        while not wave & goal_bit:
            wave = expand(wave) & free & ~seen
            if not wave:
                result = (-1, 0)
                if len(bfs_cache) < 16000:
                    bfs_cache[blocked] = result
                return result
            seen |= wave
            distance += 1
            if need_path:
                layers.append(wave)
        path = 0
        if need_path:
            path = goal_bit
            cell = goal_bit
            for layer in reversed(layers[:-1]):
                options = expand(cell) & layer
                cell = options & -options
                path |= cell
        result = (distance, path)
        if len(bfs_cache) < 16000 or blocked in bfs_cache:
            bfs_cache[blocked] = result
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
                wall = c['wall']
                if wall == 'west':
                    gap = r[0]
                elif wall == 'east':
                    gap = width - r[2]
                elif wall == 'south':
                    gap = r[1]
                else:
                    gap = depth - r[3]
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
            if shortest(fixed_blocked | b)[0] < 0:
                continue
            pp.append(o)
            rr.append(r)
            bb.append(b)
        poses.append(pp)
        rects.append(rr)
        blocks.append(bb)

    n = len(names)
    supports = [[None for j in range(n)] for i in range(n)]
    neighbors = [[] for i in range(n)]
    for i in range(n):
        for j in range(i):
            ij = [0] * len(poses[i])
            ji = [0] * len(poses[j])
            for a in range(len(poses[i])):
                for b in range(len(poses[j])):
                    if compatible(poses[i][a], rects[i][a], poses[j][b], rects[j][b]):
                        ij[a] |= 1 << b
                        ji[b] |= 1 << a
            supports[i][j] = ij
            supports[j][i] = ji
            full_j = (1 << len(poses[j])) - 1
            if any(v != full_j for v in ij):
                neighbors[i].append(j)
                neighbors[j].append(i)

    def propagate(domains, changed=None):
        if any(d == 0 for d in domains):
            return False
        if changed is None:
            queue = deque((i, j) for i in range(n) for j in neighbors[i])
        else:
            queue = deque((i, j) for j in changed for i in neighbors[j])
        while queue:
            i, j = queue.popleft()
            old = domains[i]
            values = old
            new = 0
            table = supports[i][j]
            other = domains[j]
            while values:
                bit = values & -values
                values -= bit
                if table[bit.bit_length() - 1] & other:
                    new |= bit
            if new != old:
                if not new:
                    return False
                domains[i] = new
                for k in neighbors[i]:
                    if k != j:
                        queue.append((k, i))
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

    work = [0]
    fallback = [None, -1]
    dead = set()
    access_dead = set()
    summary_cache = [{} for i in range(n)]

    def summarize(i, domain):
        cached = summary_cache[i].get(domain)
        if cached is not None:
            return cached
        common, possible = whole, 0
        values = domain
        while values:
            bit = values & -values
            values -= bit
            b = blocks[i][bit.bit_length() - 1]
            common &= b
            possible |= b
        result = (common, possible)
        if len(summary_cache[i]) < 3000:
            summary_cache[i][domain] = result
        return result

    def search(domains, changed=None):
        work[0] += 1
        if work[0] > 40000:
            return None
        if not propagate(domains, changed):
            return None
        while True:
            key = tuple(domains)
            if key in dead:
                return None
            forced = fixed_blocked
            possible = fixed_blocked
            for i, d in enumerate(domains):
                common, union = summarize(i, d)
                forced |= common
                possible |= union
            distance, path = shortest(forced, True)
            if distance < 0:
                if len(dead) < 35000:
                    dead.add(key)
                return None
            cutters = []
            if distance < minimum:
                upper, unused = shortest(possible)
                if 0 <= upper < minimum:
                    if len(dead) < 35000:
                        dead.add(key)
                    return None
                for i, d in enumerate(domains):
                    values, cutting = d, 0
                    while values:
                        bit = values & -values
                        values -= bit
                        if blocks[i][bit.bit_length() - 1] & path:
                            cutting |= bit
                    if cutting:
                        cutters.append((i, cutting))
                if not cutters:
                    if len(dead) < 35000:
                        dead.add(key)
                    return None
                if len(cutters) == 1:
                    i, cutting = cutters[0]
                    if cutting != domains[i]:
                        domains[i] = cutting
                        if not propagate(domains, [i]):
                            return None
                        continue
            break
        undecided = [i for i in range(n) if domains[i] & (domains[i] - 1)]
        if not undecided:
            indices = [d.bit_length() - 1 for d in domains]
            objects = fixed + [poses[i][indices[i]] for i in range(n)]
            geometry = tuple(rects[i][indices[i]] for i in range(n))
            if geometry not in access_dead:
                all_rects = fixed_rects + list(geometry)
                if accessible(objects, all_rects):
                    if distance >= minimum:
                        return objects
                    if distance > fallback[1]:
                        fallback[0], fallback[1] = objects, distance
                elif len(access_dead) < 4000:
                    access_dead.add(geometry)
            if len(dead) < 35000:
                dead.add(key)
            return None
        cutting_by_id = {i: d for i, d in cutters}
        i = min(undecided, key=lambda j: (domains[j].bit_count(), -int(j in cutting_by_id), -len(neighbors[j])))
        if 1 < len(cutters) <= 3 and domains[i].bit_count() > 2 and all(c != domains[j] for j, c in cutters):
            ranked = []
            for j, c in cutters:
                best_distance = -1
                hits = 0
                values = c
                while values:
                    bit = values & -values
                    values -= bit
                    a = bit.bit_length() - 1
                    d, unused = shortest(forced | blocks[j][a])
                    if d >= 0:
                        best_distance = max(best_distance, min(d, minimum))
                        hits = max(hits, (blocks[j][a] & path).bit_count())
                ranked.append((best_distance, hits, -c.bit_count(), j, c))
            ranked.sort(reverse=True)
            remainder = list(domains)
            excluded = []
            for score, hits, size, j, c in ranked:
                child = list(remainder)
                child[j] &= c
                result = search(child, excluded + [j])
                if result is not None:
                    return result
                if work[0] > 40000:
                    return None
                remainder[j] &= ~c
                excluded.append(j)
                if not remainder[j]:
                    break
            if len(dead) < 35000:
                dead.add(key)
            return None
        choices = []
        values = domains[i]
        viable = 0
        while values:
            bit = values & -values
            values -= bit
            a = bit.bit_length() - 1
            d, unused = shortest(forced | blocks[i][a])
            if d < 0:
                continue
            viable |= bit
            freedom = sum((supports[i][j][a] & domains[j]).bit_count() for j in neighbors[i] if domains[j] & (domains[j] - 1))
            hit = (blocks[i][a] & path).bit_count() if distance < minimum else 0
            o = poses[i][a]
            choices.append((min(d, minimum), hit, freedom, o['x'], o['y'], o['yaw_deg'], bit))
        if not viable:
            if len(dead) < 35000:
                dead.add(key)
            return None
        if viable != domains[i]:
            child = list(domains)
            child[i] = viable
            return search(child, [i])
        choices.sort(reverse=True)
        for choice in choices:
            child = list(domains)
            child[i] = choice[-1]
            result = search(child, [i])
            if result is not None:
                return result
            if work[0] > 40000:
                return None
        if len(dead) < 35000:
            dead.add(key)
        return None

    result = search([(1 << len(p)) - 1 for p in poses])
    if result is None:
        result = fallback[0]
    if result is None:
        result = fixed + [poses[i][0] if poses[i] else packet['domains'][names[i]][0] for i in range(n)]
    return {'scene_id': spec['scene_id'], 'objects': result}
