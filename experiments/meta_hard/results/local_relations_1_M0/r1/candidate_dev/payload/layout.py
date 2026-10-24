def layout(packet):
    import math
    from collections import deque

    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    constraints = spec.get('constraints', [])
    regions = {r['id']: r for r in spec.get('regions', [])}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    fixed = list(packet['fixed'])
    fixed_ids = {o['id'] for o in fixed}
    names = [o['id'] for o in fixed]
    names += [n for n in packet['domains'] if n not in fixed_ids]
    index = {n: i for i, n in enumerate(names)}
    count = len(names)
    references = {c['reference'] for c in constraints if c['type'] == 'in_front_of'}
    walls = {}
    relations = {}
    forbidden = []
    for c in constraints:
        if c['type'] == 'near_wall':
            walls.setdefault(c['subject'], []).append(c)
        elif c['type'] == 'in_front_of':
            relations.setdefault((c['subject'], c['reference']), []).append(c)
        elif c['type'] == 'keep_clear':
            r = regions[c['region']]
            forbidden.append((r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth']))

    def rectangle(o):
        a = assets[o['asset']]
        w, d = a['width'], a['depth']
        if o['yaw_deg'] % 180:
            w, d = d, w
        return (o['x'] - w / 2, o['y'] - d / 2,
                o['x'] + w / 2, o['y'] + d / 2)

    def overlap(a, b):
        return (min(a[2], b[2]) - max(a[0], b[0]) > 1e-8 and
                min(a[3], b[3]) - max(a[1], b[1]) > 1e-8)

    def unary(o, r):
        if o['id'] in required and o['asset'] != required[o['id']]:
            return False
        if r[0] < -1e-8 or r[1] < -1e-8 or r[2] > width + 1e-8 or r[3] > depth + 1e-8:
            return False
        if any(overlap(r, z) for z in forbidden):
            return False
        for c in walls.get(o['id'], []):
            gap = {'west': r[0], 'east': width - r[2],
                   'south': r[1], 'north': depth - r[3]}[c['wall']]
            if gap < -1e-8 or gap > c['max_gap'] + 1e-8:
                return False
        return True

    def front(o, r, ref, rr, cs):
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[(ref['yaw_deg'] % 360) // 90]
        dx, dy = o['x'] - ref['x'], o['y'] - ref['y']
        extent = (abs(fx) * (r[2] - r[0] + rr[2] - rr[0]) +
                  abs(fy) * (r[3] - r[1] + rr[3] - rr[1])) / 2
        lateral = (abs(fy) * (r[2] - r[0] + rr[2] - rr[0]) +
                   abs(fx) * (r[3] - r[1] + rr[3] - rr[1])) / 2
        gap = dx * fx + dy * fy - extent
        if gap < -1e-8 or abs(dx * fy - dy * fx) > lateral + 1e-8:
            return False
        return all(gap <= c['max_gap'] + 1e-8 for c in cs)

    gw, gh = int(width), int(depth)
    size = gw * gh
    full = (1 << size) - 1
    left = sum(1 << (y * gw) for y in range(gh))
    right = left << (gw - 1)
    not_left, not_right = full ^ left, full ^ right
    start = 1 << (packet['start'][1] * gw + packet['start'][0])
    goal = 1 << (packet['goal'][1] * gw + packet['goal'][0])
    endpoints = start | goal
    minimum = packet['minimum_steps']

    def neighbors(bits):
        return (((bits & not_right) << 1) | ((bits & not_left) >> 1) |
                (bits << gw) | (bits >> gw)) & full

    def cell_mask(r, clearance, nx, ny, step):
        mask = 0
        x0 = max(0, int(math.floor((r[0] - clearance) / step - 0.5)))
        x1 = min(nx - 1, int(math.ceil((r[2] + clearance) / step - 0.5)))
        y0 = max(0, int(math.floor((r[1] - clearance) / step - 0.5)))
        y1 = min(ny - 1, int(math.ceil((r[3] + clearance) / step - 0.5)))
        row = 0
        for x in range(x0, x1 + 1):
            if r[0] - clearance < (x + 0.5) * step < r[2] + clearance:
                row |= 1 << x
        for y in range(y0, y1 + 1):
            if r[1] - clearance < (y + 0.5) * step < r[3] + clearance:
                mask |= row << (y * nx)
        return mask

    poses, rects, blocks = [], [], []
    fixed_map = {o['id']: o for o in fixed}
    for name in names:
        source = [fixed_map[name]] if name in fixed_map else packet['domains'][name]
        pp, rr, bb = [], [], []
        seen = set()
        for o in source:
            if o['id'] != name:
                continue
            r = rectangle(o)
            if not unary(o, r):
                continue
            b = cell_mask(r, 0.15, gw, gh, 1.0)
            if b & endpoints:
                continue
            key = (r, o['yaw_deg'] % 360 if name in references else 0)
            if key in seen:
                continue
            seen.add(key)
            pp.append(o)
            rr.append(r)
            bb.append(b)
        poses.append(pp)
        rects.append(rr)
        blocks.append(bb)

    def result(selection):
        objects = list(fixed)
        for i, name in enumerate(names):
            if name not in fixed_ids:
                objects.append(poses[i][selection[i]])
        return {'scene_id': spec['scene_id'], 'objects': objects}

    if any(not p for p in poses):
        objects = list(fixed)
        objects += [packet['domains'][n][0] for n in names
                    if n not in fixed_ids and packet['domains'][n]]
        return {'scene_id': spec['scene_id'], 'objects': objects}

    compatibility = [[None for j in range(count)] for i in range(count)]
    for i in range(count):
        for j in range(i + 1, count):
            forward = relations.get((names[i], names[j]), [])
            backward = relations.get((names[j], names[i]), [])
            ij = [0] * len(poses[i])
            ji = [0] * len(poses[j])
            for a, o in enumerate(poses[i]):
                r = rects[i][a]
                for b, q in enumerate(poses[j]):
                    s = rects[j][b]
                    if overlap(r, s):
                        continue
                    if forward and not front(o, r, q, s, forward):
                        continue
                    if backward and not front(q, s, o, r, backward):
                        continue
                    ij[a] |= 1 << b
                    ji[b] |= 1 << a
            compatibility[i][j] = ij
            compatibility[j][i] = ji

    work = [0]
    def propagate(domains, changed):
        queue = deque((i, j) for j in changed for i in range(count) if i != j)
        while queue:
            i, j = queue.popleft()
            work[0] += 1
            old, other = domains[i], domains[j]
            keep = 0
            remaining = old
            table = compatibility[i][j]
            while remaining:
                bit = remaining & -remaining
                remaining -= bit
                if table[bit.bit_length() - 1] & other:
                    keep |= bit
            if keep != old:
                if not keep:
                    return False
                domains[i] = keep
                queue.extend((k, i) for k in range(count) if k != i and k != j)
        return True

    nav_cache = {}
    def navigation(blocked):
        cached = nav_cache.get(blocked)
        if cached is not None:
            return cached
        if blocked & endpoints:
            return (-1, 0)
        allowed = full ^ blocked
        frontier, visited = start, start
        layers = [start]
        distance = 0
        while frontier and not frontier & goal:
            frontier = neighbors(frontier) & allowed & ~visited
            visited |= frontier
            layers.append(frontier)
            distance += 1
        if not frontier:
            answer = (-1, 0)
        else:
            point, path = goal, goal
            for layer in reversed(layers[:-1]):
                candidates = neighbors(point) & layer
                point = candidates & -candidates
                path |= point
            answer = (distance, path)
        if len(nav_cache) < 12000:
            nav_cache[blocked] = answer
        return answer

    access_paths = spec.get('access_paths', [])
    fine_step = 0.2
    fw = int(math.ceil(width / fine_step - 1e-9))
    fh = int(math.ceil(depth / fine_step - 1e-9))
    fine_full = (1 << (fw * fh)) - 1
    fine_left = sum(1 << (y * fw) for y in range(fh))
    fine_right = fine_left << (fw - 1)
    fine_nl, fine_nr = fine_full ^ fine_left, fine_full ^ fine_right
    fine_masks = {}
    source_masks = {}
    target_masks = {}
    access_cache = {}

    def access_ok(selection):
        for ap in access_paths:
            clearance = ap.get('clearance', 0.15)
            if clearance not in fine_masks:
                fine_masks[clearance] = [[cell_mask(r, clearance, fw, fh, fine_step)
                                         for r in row] for row in rects]
            masks = fine_masks[clearance]
            blocked = 0
            for i, value in enumerate(selection):
                blocked |= masks[i][value]
            region_id = ap['from_region']
            if region_id not in source_masks:
                z = regions[region_id]
                source_masks[region_id] = cell_mask(
                    (z['x'], z['y'], z['x'] + z['width'], z['y'] + z['depth']),
                    0.0, fw, fh, fine_step)
            ti = index[ap['to_object']]
            tv = selection[ti]
            interaction = ap.get('interaction_distance', 0.8)
            tk = (ti, tv, interaction)
            if tk not in target_masks:
                r = rects[ti][tv]
                target = 0
                for y in range(fh):
                    py = (y + 0.5) * fine_step
                    dy = max(r[1] - py, 0.0, py - r[3])
                    if dy > interaction:
                        continue
                    for x in range(fw):
                        px = (x + 0.5) * fine_step
                        dx = max(r[0] - px, 0.0, px - r[2])
                        if dx * dx + dy * dy <= interaction * interaction + 1e-9:
                            target |= 1 << (y * fw + x)
                target_masks[tk] = target
            key = (blocked, region_id, tk)
            cached = access_cache.get(key)
            if cached is not None:
                if not cached:
                    return False
                continue
            allowed = fine_full ^ blocked
            frontier = source_masks[region_id] & allowed
            visited = frontier
            target = target_masks[tk] & allowed
            while frontier and not frontier & target:
                frontier = (((frontier & fine_nr) << 1) |
                            ((frontier & fine_nl) >> 1) |
                            (frontier << fw) | (frontier >> fw)) & allowed & ~visited
                visited |= frontier
            ok = bool(frontier & target)
            if len(access_cache) < 4000:
                access_cache[key] = ok
            if not ok:
                return False
        return True

    nodes = [0]
    best = [-1, None]
    def search(domains, changed):
        nodes[0] += 1
        if nodes[0] > 24000 or work[0] > 2200000:
            return None
        if not propagate(domains, changed):
            return None
        mandatory = 0
        possible = 0
        unresolved = []
        selection = [0] * count
        for i, domain in enumerate(domains):
            remaining = domain
            intersection = full
            union = 0
            while remaining:
                bit = remaining & -remaining
                remaining -= bit
                b = blocks[i][bit.bit_length() - 1]
                intersection &= b
                union |= b
            mandatory |= intersection
            possible |= union
            if domain & (domain - 1):
                unresolved.append(i)
            else:
                selection[i] = domain.bit_length() - 1
        distance, path = navigation(mandatory)
        if distance < 0:
            return None
        if not unresolved:
            if access_ok(selection):
                if distance > best[0]:
                    best[:] = [distance, selection[:]]
                if distance >= minimum:
                    return selection
            return None
        hitters = {}
        if distance < minimum:
            for i in unresolved:
                remaining, hit = domains[i], 0
                while remaining:
                    bit = remaining & -remaining
                    remaining -= bit
                    if blocks[i][bit.bit_length() - 1] & path:
                        hit |= bit
                if hit:
                    hitters[i] = hit
            if not hitters:
                return None
            if len(hitters) == 1:
                i = next(iter(hitters))
                if hitters[i] != domains[i]:
                    narrowed = domains[:]
                    narrowed[i] = hitters[i]
                    return search(narrowed, [i])
            if len(unresolved) <= 3:
                upper, unused = navigation(possible)
                if 0 <= upper < minimum:
                    return None
        i = min(unresolved, key=lambda j: (
            domains[j].bit_count(),
            -hitters.get(j, 0).bit_count(),
            -sum(bool(relations.get((names[j], names[k])) or
                      relations.get((names[k], names[j]))) for k in unresolved)))
        choices = []
        remaining = domains[i]
        while remaining:
            bit = remaining & -remaining
            remaining -= bit
            value = bit.bit_length() - 1
            b = blocks[i][value]
            d, unused = navigation(mandatory | b)
            if d < 0:
                continue
            impact = (b & path).bit_count()
            choices.append((min(d, minimum), impact if d < minimum else -b.bit_count(), bit))
        choices.sort(reverse=True)
        for unused_d, unused_impact, bit in choices:
            narrowed = domains[:]
            narrowed[i] = bit
            answer = search(narrowed, [i])
            if answer is not None:
                return answer
            if nodes[0] > 24000 or work[0] > 2200000:
                break
        return None

    domains = [(1 << len(p)) - 1 for p in poses]
    answer = search(domains, list(range(count)))
    if answer is None:
        answer = best[1]
    if answer is None:
        answer = [0] * count
    return result(answer)
