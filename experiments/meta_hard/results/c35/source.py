def layout(packet):
    import math
    from collections import deque

    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    fixed = list(packet['fixed'])
    fixed_map = {o['id']: o for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    names = list(fixed_map)
    names.extend(n for n in required if n not in fixed_map)
    count = len(names)
    index = {n: i for i, n in enumerate(names)}
    regions = {r['id']: r for r in spec.get('regions', [])}
    walls, relations, forbidden = {}, {}, []
    references = set()
    for c in spec.get('constraints', []):
        kind = c['type']
        if kind == 'near_wall':
            walls.setdefault(c['subject'], []).append(c)
        elif kind == 'in_front_of':
            relations.setdefault((c['subject'], c['reference']), []).append(c)
            references.add(c['reference'])
        elif kind == 'keep_clear':
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
        for z in forbidden:
            if overlap(r, z):
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
        sx = (r[2] - r[0] + rr[2] - rr[0]) / 2
        sy = (r[3] - r[1] + rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * sx - abs(fy) * sy
        lateral = abs(fy) * sx + abs(fx) * sy
        if gap < -1e-8 or abs(dx * fy - dy * fx) > lateral + 1e-8:
            return False
        return all(gap <= c['max_gap'] + 1e-8 for c in cs)

    gw, gh = int(width), int(depth)
    full = (1 << (gw * gh)) - 1
    left = sum(1 << (y * gw) for y in range(gh))
    not_left = full ^ left
    not_right = full ^ (left << (gw - 1))
    start = 1 << (packet['start'][1] * gw + packet['start'][0])
    goal = 1 << (packet['goal'][1] * gw + packet['goal'][0])
    endpoints = start | goal
    minimum = packet['minimum_steps']

    def neighbors(bits):
        return (((bits & not_right) << 1) | ((bits & not_left) >> 1) |
                (bits << gw) | (bits >> gw)) & full

    def cell_mask(r, clearance, nx, ny, step):
        x0 = max(0, int(math.floor((r[0] - clearance) / step - 0.5)))
        x1 = min(nx - 1, int(math.ceil((r[2] + clearance) / step - 0.5)))
        y0 = max(0, int(math.floor((r[1] - clearance) / step - 0.5)))
        y1 = min(ny - 1, int(math.ceil((r[3] + clearance) / step - 0.5)))
        row = 0
        for x in range(x0, x1 + 1):
            if r[0] - clearance < (x + 0.5) * step < r[2] + clearance:
                row |= 1 << x
        mask = 0
        for y in range(y0, y1 + 1):
            if r[1] - clearance < (y + 0.5) * step < r[3] + clearance:
                mask |= row << (y * nx)
        return mask

    poses, rects, blocks = [], [], []
    for name in names:
        source = [fixed_map[name]] if name in fixed_map else packet['domains'][name]
        pp, rr, bb = [], [], []
        seen = set()
        for o in source:
            if o['id'] != name or o['asset'] not in assets:
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
            if name not in fixed_map:
                objects.append(poses[i][selection[i]])
        return {'scene_id': spec['scene_id'], 'objects': objects}

    if any(not p for p in poses):
        objects = list(fixed)
        for name in names:
            if name not in fixed_map and packet['domains'][name]:
                objects.append(packet['domains'][name][0])
        return {'scene_id': spec['scene_id'], 'objects': objects}

    initial = [(1 << len(p)) - 1 for p in poses]
    compatibility = [[None] * count for i in range(count)]
    adjacent = [[] for i in range(count)]
    relation_degree = [0] * count
    for i in range(count):
        for j in range(i + 1, count):
            forward = relations.get((names[i], names[j]), [])
            backward = relations.get((names[j], names[i]), [])
            if forward or backward:
                relation_degree[i] += 1
                relation_degree[j] += 1
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
            if any(v != initial[j] for v in ij):
                compatibility[i][j] = ij
                compatibility[j][i] = ji
                adjacent[i].append(j)
                adjacent[j].append(i)

    budget = [0, 0, 0, False]

    def exhausted():
        if budget[0] > 24000 or budget[1] > 1800000 or budget[2] > 450000:
            budget[3] = True
        return budget[3]

    def propagate(domains, changed):
        queue = deque()
        pending = set()
        for j in changed:
            for i in adjacent[j]:
                key = i * count + j
                if key not in pending:
                    pending.add(key)
                    queue.append((i, j))
        while queue:
            i, j = queue.popleft()
            pending.discard(i * count + j)
            old, other = domains[i], domains[j]
            table = compatibility[i][j]
            keep, remaining = 0, old
            while remaining:
                bit = remaining & -remaining
                remaining -= bit
                budget[1] += 1
                if table[bit.bit_length() - 1] & other:
                    keep |= bit
            if keep != old:
                if not keep:
                    return False
                domains[i] = keep
                for k in adjacent[i]:
                    if k != j:
                        key = k * count + i
                        if key not in pending:
                            pending.add(key)
                            queue.append((k, i))
            if exhausted():
                return False
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
        budget[2] += distance + 1
        if not frontier:
            answer = (-1, 0)
        else:
            point, path = goal, goal
            for layer in reversed(layers[:-1]):
                candidates = neighbors(point) & layer
                point = candidates & -candidates
                path |= point
            answer = (distance, path)
        if len(nav_cache) < 16000:
            nav_cache[blocked] = answer
        return answer

    bound_cache = [{} for i in range(count)]

    def blockage(i, domain):
        cached = bound_cache[i].get(domain)
        if cached is not None:
            return cached
        intersection, union = full, 0
        remaining = domain
        while remaining:
            bit = remaining & -remaining
            remaining -= bit
            b = blocks[i][bit.bit_length() - 1]
            intersection &= b
            union |= b
        answer = (intersection, union)
        if len(bound_cache[i]) < 4096:
            bound_cache[i][domain] = answer
        return answer

    clauses, clause_paths = [], set()

    def learn_path(path):
        if path in clause_paths:
            return
        terms = []
        for i in range(count):
            hit = 0
            for v, b in enumerate(blocks[i]):
                if b & path:
                    hit |= 1 << v
            if hit:
                terms.append((i, hit))
        if len(clauses) >= 96:
            old_path, unused = clauses.pop(0)
            clause_paths.discard(old_path)
        clauses.append((path, terms))
        clause_paths.add(path)

    def stabilize(domains, changed):
        if not propagate(domains, changed):
            return False
        while True:
            changed = []
            for unused_path, terms in clauses:
                satisfied = False
                supported = 0
                last_i, last_hit = -1, 0
                for i, hit in terms:
                    budget[1] += 1
                    domain = domains[i]
                    available = domain & hit
                    if available == domain:
                        satisfied = True
                        break
                    if available:
                        supported += 1
                        last_i, last_hit = i, available
                if satisfied:
                    continue
                if not supported:
                    return False
                if supported == 1:
                    domains[last_i] = last_hit
                    changed.append(last_i)
            if exhausted():
                return False
            if not changed:
                return True
            if not propagate(domains, changed):
                return False

    access_paths = spec.get('access_paths', [])
    fine_step = 0.2
    fw = int(math.ceil(width / fine_step - 1e-9))
    fh = int(math.ceil(depth / fine_step - 1e-9))
    fine_full = (1 << (fw * fh)) - 1
    fine_left = sum(1 << (y * fw) for y in range(fh))
    fine_nl = fine_full ^ fine_left
    fine_nr = fine_full ^ (fine_left << (fw - 1))
    fine_masks, source_masks, target_masks, access_cache = {}, {}, {}, {}

    def access_ok(selection):
        for ap_index, ap in enumerate(access_paths):
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
                    row = 0
                    for x in range(fw):
                        px = (x + 0.5) * fine_step
                        dx = max(r[0] - px, 0.0, px - r[2])
                        if dx * dx + dy * dy <= interaction * interaction + 1e-9:
                            row |= 1 << x
                    target |= row << (y * fw)
                target_masks[tk] = target
            key = (blocked, ap_index, tv)
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

    failed = set()
    best = [-1, None]

    def search(domains, changed):
        budget[0] += 1
        if exhausted() or not stabilize(domains, changed):
            return None
        state = tuple(domains)
        if state in failed:
            return None
        mandatory, possible = 0, 0
        unresolved = []
        selection = [0] * count
        for i, domain in enumerate(domains):
            intersection, union = blockage(i, domain)
            mandatory |= intersection
            possible |= union
            if domain & (domain - 1):
                unresolved.append(i)
            else:
                selection[i] = domain.bit_length() - 1
        distance, path = navigation(mandatory)
        if distance < 0:
            if len(failed) < 24000:
                failed.add(state)
            return None
        if not unresolved:
            if access_ok(selection):
                if distance > best[0]:
                    best[:] = [distance, selection[:]]
                if distance >= minimum:
                    return selection
            if len(failed) < 24000:
                failed.add(state)
            return None
        hitters = {}
        if distance < minimum:
            learn_path(path)
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
                if len(failed) < 24000:
                    failed.add(state)
                return None
            if len(hitters) == 1:
                i = next(iter(hitters))
                if hitters[i] != domains[i]:
                    narrowed = domains[:]
                    narrowed[i] = hitters[i]
                    return search(narrowed, [i])
            if len(unresolved) <= 4:
                upper, unused = navigation(possible)
                if 0 <= upper < minimum:
                    if len(failed) < 24000:
                        failed.add(state)
                    return None
        if exhausted():
            return None
        candidates = list(hitters) if hitters else unresolved
        i = min(candidates, key=lambda j: (
            domains[j].bit_count(),
            -relation_degree[j],
            -len(adjacent[j]),
            -hitters.get(j, 0).bit_count()))
        preferred = hitters.get(i, domains[i])
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
            support = 0
            for j in adjacent[i]:
                if domains[j] & (domains[j] - 1):
                    support += (compatibility[i][j][value] & domains[j]).bit_count()
            impact = (b & path).bit_count()
            choices.append((bool(preferred & bit), min(d, minimum),
                            impact if d < minimum else -b.bit_count(), support, bit))
        choices.sort(reverse=True)
        for unused_hit, unused_d, unused_impact, unused_support, bit in choices:
            narrowed = domains[:]
            narrowed[i] = bit
            answer = search(narrowed, [i])
            if answer is not None:
                return answer
            if exhausted():
                return None
        if len(failed) < 24000:
            failed.add(state)
        return None

    answer = search(initial[:], list(range(count)))
    if answer is None:
        answer = best[1]
    if answer is None:
        answer = [0] * count
    return result(answer)
