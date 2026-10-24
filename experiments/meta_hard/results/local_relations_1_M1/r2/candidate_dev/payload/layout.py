import math
from collections import deque

def layout(packet):
    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    fixed = list(packet['fixed'])
    fixed_by_id = {o['id']: o for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    names = list(fixed_by_id)
    names.extend(o['id'] for o in spec['required_objects'] if o['id'] not in fixed_by_id)
    index = {name: i for i, name in enumerate(names)}
    n = len(names)
    eps = 1e-8
    W, H = int(width), int(depth)
    board = (1 << (W * H)) - 1
    left = sum(1 << (y * W) for y in range(H))
    right = left << (W - 1)
    nl, nr = board ^ left, board ^ right
    sb = 1 << (packet['start'][1] * W + packet['start'][0])
    gb = 1 << (packet['goal'][1] * W + packet['goal'][0])
    endpoints = sb | gb
    minimum = packet['minimum_steps']
    regions = {r['id']: r for r in spec.get('regions', [])}
    unary = [[] for _ in names]
    relations = {}
    references = set()
    clear_regions = []
    for c in spec.get('constraints', []):
        kind = c['type']
        if kind == 'keep_clear':
            r = regions[c['region']]
            clear_regions.append((r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth']))
        elif kind == 'near_wall':
            unary[index[c['subject']]].append(c)
        elif kind == 'in_front_of':
            pair = (index[c['subject']], index[c['reference']])
            relations.setdefault(pair, []).append(c)
            references.add(c['reference'])

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

    def front_ok(o, r, ref, rr, c):
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[int(ref['yaw_deg'] % 360 // 90)]
        dx, dy = o['x'] - ref['x'], o['y'] - ref['y']
        hx = (r[2] - r[0] + rr[2] - rr[0]) / 2
        hy = (r[3] - r[1] + rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * hx - abs(fy) * hy
        return (-eps <= gap <= c['max_gap'] + eps and
                abs(dx * fy - dy * fx) <= abs(fy) * hx + abs(fx) * hy + eps)

    def grid_mask(r, clearance, nx, ny, step):
        x0 = max(0, int(math.floor((r[0] - clearance) / step - 0.5)) + 1)
        x1 = min(nx - 1, int(math.ceil((r[2] + clearance) / step - 0.5)) - 1)
        y0 = max(0, int(math.floor((r[1] - clearance) / step - 0.5)) + 1)
        y1 = min(ny - 1, int(math.ceil((r[3] + clearance) / step - 0.5)) - 1)
        if x1 < x0 or y1 < y0:
            return 0
        row = ((1 << (x1 - x0 + 1)) - 1) << x0
        result = 0
        for y in range(y0, y1 + 1):
            result |= row << (y * nx)
        return result

    poses, rects, masks = [], [], []
    for i, name in enumerate(names):
        source = [fixed_by_id[name]] if name in fixed_by_id else packet['domains'].get(name, [])
        pp, rr, mm = [], [], []
        seen = set()
        for o in source:
            if o['id'] != name or o['asset'] != required.get(name, o['asset']):
                continue
            r = rectangle(o)
            if r[0] < -eps or r[1] < -eps or r[2] > width + eps or r[3] > depth + eps:
                continue
            if any(overlap(r, z) for z in clear_regions):
                continue
            good = True
            for c in unary[i]:
                gap = {'west': r[0], 'east': width - r[2],
                       'south': r[1], 'north': depth - r[3]}[c['wall']]
                if not -eps <= gap <= c['max_gap'] + eps:
                    good = False
                    break
            if not good:
                continue
            if any(not front_ok(o, r, o, r, c) for c in relations.get((i, i), [])):
                continue
            m = grid_mask(r, 0.15, W, H, 1.0)
            if m & endpoints:
                continue
            key = (r, o['yaw_deg'] % 360 if name in references else 0)
            if key in seen:
                continue
            seen.add(key)
            pp.append(o)
            rr.append(r)
            mm.append(m)
        poses.append(pp)
        rects.append(rr)
        masks.append(mm)

    def output(chosen):
        objects = list(fixed)
        if chosen is None:
            for i, name in enumerate(names):
                if name not in fixed_by_id:
                    source = poses[i] or packet['domains'].get(name, [])
                    if source:
                        objects.append(source[0])
        else:
            objects.extend(poses[i][chosen[i]] for i, name in enumerate(names) if name not in fixed_by_id)
        return {'scene_id': spec['scene_id'], 'objects': objects}

    if any(not p for p in poses):
        return output(None)
    support = [[None] * n for _ in names]
    neighbors = [[] for _ in names]
    for i in range(n):
        for j in range(i + 1, n):
            forward = [0] * len(poses[i])
            backward = [0] * len(poses[j])
            cs, cr = relations.get((i, j), []), relations.get((j, i), [])
            restrictive = False
            for a, o in enumerate(poses[i]):
                r = rects[i][a]
                for b, ref in enumerate(poses[j]):
                    rr = rects[j][b]
                    good = not overlap(r, rr)
                    if good:
                        good = all(front_ok(o, r, ref, rr, c) for c in cs)
                    if good:
                        good = all(front_ok(ref, rr, o, r, c) for c in cr)
                    if good:
                        forward[a] |= 1 << b
                        backward[b] |= 1 << a
                    else:
                        restrictive = True
            support[i][j], support[j][i] = forward, backward
            if restrictive:
                neighbors[i].append(j)
                neighbors[j].append(i)

    work = [0]
    budget = 1800000

    def propagate(state, changed):
        queue = deque(changed)
        pending = set(changed)
        while queue:
            j = queue.popleft()
            pending.discard(j)
            dj = state[j]
            for i in neighbors[j]:
                old = state[i]
                bits = old
                revised = 0
                table = support[i][j]
                work[0] += old.bit_count()
                while bits:
                    bit = bits & -bits
                    bits -= bit
                    if table[bit.bit_length() - 1] & dj:
                        revised |= bit
                if not revised:
                    return False
                if revised != old:
                    state[i] = revised
                    if i not in pending:
                        pending.add(i)
                        queue.append(i)
        return True

    def expand(bits):
        return (((bits & nr) << 1) | ((bits & nl) >> 1) |
                (bits << W) | (bits >> W)) & board

    nav_cache = {}

    def navigation(blocked):
        cached = nav_cache.get(blocked)
        if cached is not None:
            return cached
        if blocked & endpoints:
            return (-1, 0)
        free = board ^ blocked
        frontier = sb
        visited = sb
        layers = []
        distance = 0
        answer = (-1, 0)
        while frontier:
            work[0] += 4
            if distance < minimum:
                layers.append(frontier)
            if frontier & gb:
                path = 0
                if distance < minimum:
                    bit = gb
                    path = bit
                    for k in range(distance - 1, -1, -1):
                        options = expand(bit) & layers[k]
                        bit = options & -options
                        path |= bit
                answer = (distance, path)
                break
            frontier = expand(frontier) & free & ~visited
            visited |= frontier
            distance += 1
        if len(nav_cache) < 6000:
            nav_cache[blocked] = answer
        return answer

    summaries = [{} for _ in names]

    def summary(i, domain):
        cached = summaries[i].get(domain)
        if cached is not None:
            return cached
        bits = domain
        common, union = board, 0
        work[0] += domain.bit_count()
        while bits:
            bit = bits & -bits
            bits -= bit
            m = masks[i][bit.bit_length() - 1]
            common &= m
            union |= m
        answer = (common, union)
        if len(summaries[i]) < 2048:
            summaries[i][domain] = answer
        return answer

    access_specs = spec.get('access_paths', [])
    fine = [None]

    def prepare_access():
        step = 0.1
        fw, fh = int(math.ceil(width / step)), int(math.ceil(depth / step))
        fb = (1 << (fw * fh)) - 1
        fl = sum(1 << (y * fw) for y in range(fh))
        fnl, fnr = fb ^ fl, fb ^ (fl << (fw - 1))
        tables = {}
        entries = []
        for access in access_specs:
            clearance = access.get('clearance', 0.15)
            if clearance not in tables:
                tables[clearance] = [[grid_mask(r, clearance, fw, fh, step) for r in rs] for rs in rects]
            region = regions[access['from_region']]
            seeds = 0
            for y in range(fh):
                cy = (y + 0.5) * step
                if not region['y'] - eps <= cy <= region['y'] + region['depth'] + eps or cy > depth:
                    continue
                row = 0
                for x in range(fw):
                    cx = (x + 0.5) * step
                    if region['x'] - eps <= cx <= region['x'] + region['width'] + eps and cx <= width:
                        row |= 1 << x
                seeds |= row << (y * fw)
            target = index[access['to_object']]
            distance = access.get('interaction_distance', 0.8)
            targets = []
            for o in poses[target]:
                target_mask = 0
                x0 = max(0, int((o['x'] - distance) / step) - 1)
                x1 = min(fw - 1, int((o['x'] + distance) / step) + 1)
                y0 = max(0, int((o['y'] - distance) / step) - 1)
                y1 = min(fh - 1, int((o['y'] + distance) / step) + 1)
                for y in range(y0, y1 + 1):
                    cy = (y + 0.5) * step
                    row = 0
                    for x in range(x0, x1 + 1):
                        cx = (x + 0.5) * step
                        if cx <= width and cy <= depth and (cx - o['x']) ** 2 + (cy - o['y']) ** 2 <= distance * distance + eps:
                            row |= 1 << x
                    target_mask |= row << (y * fw)
                targets.append(target_mask)
            entries.append((tables[clearance], seeds, target, targets))
        fine[0] = (fw, fb, fnl, fnr, entries)

    access_cache = {}

    def accessible(chosen):
        if not access_specs:
            return True
        key = tuple(chosen)
        cached = access_cache.get(key)
        if cached is not None:
            return cached
        if fine[0] is None:
            prepare_access()
        fw, fb, fnl, fnr, entries = fine[0]
        for table, seeds, target, targets in entries:
            blocked = 0
            for i, a in enumerate(chosen):
                blocked |= table[i][a]
            free = fb ^ blocked
            reached = seeds & free
            frontier = reached
            destination = targets[chosen[target]] & free
            if not reached or not destination:
                if len(access_cache) < 512:
                    access_cache[key] = False
                return False
            while frontier and not reached & destination:
                work[0] += 8
                adjacent = (((frontier & fnr) << 1) | ((frontier & fnl) >> 1) |
                            (frontier << fw) | (frontier >> fw)) & fb
                frontier = adjacent & free & ~reached
                reached |= frontier
            if not reached & destination:
                if len(access_cache) < 512:
                    access_cache[key] = False
                return False
        if len(access_cache) < 512:
            access_cache[key] = True
        return True

    initial = [(1 << len(p)) - 1 for p in poses]
    if not propagate(initial, range(n)):
        return output(None)
    best = [None, -1]
    result = [None]
    nodes = [0]
    failed = set()
    seed_nodes = [0]

    def search(state, difficulty):
        if work[0] >= budget or nodes[0] >= 18000:
            return False
        if not difficulty:
            seed_nodes[0] += 1
            if seed_nodes[0] > 160:
                return False
        nodes[0] += 1
        work[0] += 10
        signature = tuple(state)
        if difficulty and signature in failed:
            return False
        mandatory, possible = 0, 0
        variable = -1
        variable_key = None
        unions = [0] * n
        for i, domain in enumerate(state):
            common, union = summary(i, domain)
            mandatory |= common
            possible |= union
            unions[i] = union
            size = domain.bit_count()
            if size > 1:
                key = (size, -sum(state[j].bit_count() > 1 for j in neighbors[i]))
                if variable_key is None or key < variable_key:
                    variable_key, variable = key, i
        distance, path = navigation(mandatory)
        if distance < 0:
            if difficulty and len(failed) < 14000:
                failed.add(signature)
            return False
        if variable < 0:
            chosen = [d.bit_length() - 1 for d in state]
            if (distance >= minimum or not difficulty) and accessible(chosen):
                if distance > best[1]:
                    best[0], best[1] = chosen, distance
                if distance >= minimum:
                    result[0] = chosen
                return True
            if difficulty and len(failed) < 14000:
                failed.add(signature)
            return False

        split = None
        if difficulty and distance < minimum:
            # This particular short path must be hit by a selected footprint.
            # Domains are split into hit/miss sets, so both branches are disjoint.
            blockers = []
            for i, domain in enumerate(state):
                if domain & (domain - 1) == 0 or not unions[i] & path:
                    continue
                hits = 0
                bits = domain
                work[0] += domain.bit_count()
                while bits:
                    bit = bits & -bits
                    bits -= bit
                    if masks[i][bit.bit_length() - 1] & path:
                        hits |= bit
                if hits:
                    blockers.append((i, hits))
            if not blockers:
                if len(failed) < 14000:
                    failed.add(signature)
                return False
            if len(blockers) == 1:
                variable, hits = blockers[0]
                split = [hits]
            else:
                selected = None
                selected_key = None
                for i, hits in blockers:
                    count = hits.bit_count()
                    size = state[i].bit_count()
                    key = (count / size, count, -len(neighbors[i]))
                    if selected_key is None or key < selected_key:
                        selected_key, selected = key, (i, hits)
                variable, hits = selected
                split = [hits, state[variable] ^ hits]

        if split is None:
            choices = []
            bits = state[variable]
            while bits:
                bit = bits & -bits
                bits -= bit
                a = bit.bit_length() - 1
                retained = sum((support[variable][j][a] & state[j]).bit_count() for j in neighbors[variable])
                hit = (masks[variable][a] & path).bit_count()
                choices.append(((hit if distance < minimum else -masks[variable][a].bit_count(), retained), bit))
            choices.sort(reverse=True)
            split = [bit for score, bit in choices]
        elif len(split) == 1 and split[0] == state[variable]:
            # Normally impossible because the mandatory footprint leaves path free;
            # retain a strict-progress guard for floating-point-equivalent domains.
            bits = split[0]
            split = []
            while bits:
                bit = bits & -bits
                bits -= bit
                split.append(bit)

        for domain in split:
            if not domain or work[0] >= budget or nodes[0] >= 18000:
                continue
            child = state[:]
            child[variable] = domain
            if propagate(child, [variable]) and search(child, difficulty):
                return True
        if difficulty and work[0] < budget and nodes[0] < 18000 and len(failed) < 14000:
            failed.add(signature)
        return False

    # Obtain a checked complete fallback with a small share of the budget.
    search(initial, False)
    if result[0] is None and work[0] < budget:
        search(initial, True)
    return output(result[0] if result[0] is not None else best[0])
