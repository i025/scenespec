from collections import deque
import math

def layout(packet):
    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    constraints = spec.get('constraints', [])
    regions = {r['id']: r for r in spec.get('regions', [])}
    fixed = list(packet['fixed'])
    fixed_by_id = {o['id']: o for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    names = list(fixed_by_id)
    names.extend(o['id'] for o in spec['required_objects'] if o['id'] not in fixed_by_id)
    index = {name: i for i, name in enumerate(names)}
    n = len(names)
    eps = 1e-8
    W, H = int(width), int(depth)
    total = W * H
    board = (1 << total) - 1
    left = sum(1 << (y * W) for y in range(H))
    right = left << (W - 1)
    not_left, not_right = board ^ left, board ^ right
    start = packet['start'][1] * W + packet['start'][0]
    goal = packet['goal'][1] * W + packet['goal'][0]
    sb, gb = 1 << start, 1 << goal
    endpoints = sb | gb
    minimum = packet['minimum_steps']

    def rectangle(o):
        a = assets[o['asset']]
        w, d = a['width'], a['depth']
        if o['yaw_deg'] % 180:
            w, d = d, w
        return (o['x'] - w / 2, o['y'] - d / 2,
                o['x'] + w / 2, o['y'] + d / 2)

    def overlap(a, b):
        return min(a[2], b[2]) - max(a[0], b[0]) > eps and min(a[3], b[3]) - max(a[1], b[1]) > eps

    clear_regions = []
    unary = [[] for _ in names]
    relations = {}
    references = set()
    for c in constraints:
        kind = c['type']
        if kind == 'keep_clear':
            r = regions[c['region']]
            clear_regions.append((r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth']))
        elif kind == 'near_wall':
            unary[index[c['subject']]].append(c)
        elif kind == 'in_front_of':
            i, j = index[c['subject']], index[c['reference']]
            relations.setdefault((i, j), []).append(c)
            references.add(c['reference'])

    def front_ok(o, r, ref, rr, c):
        yaw = ref['yaw_deg'] % 360
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[int(yaw // 90)]
        dx, dy = o['x'] - ref['x'], o['y'] - ref['y']
        hx = (r[2] - r[0] + rr[2] - rr[0]) / 2
        hy = (r[3] - r[1] + rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * hx - abs(fy) * hy
        lateral = abs(dx * fy - dy * fx)
        return -eps <= gap <= c['max_gap'] + eps and lateral <= abs(fy) * hx + abs(fx) * hy + eps

    def grid_mask(r, clearance, nx, ny, step):
        x0 = max(0, int(math.floor((r[0] - clearance) / step - 0.5)) + 1)
        x1 = min(nx - 1, int(math.ceil((r[2] + clearance) / step - 0.5)) - 1)
        y0 = max(0, int(math.floor((r[1] - clearance) / step - 0.5)) + 1)
        y1 = min(ny - 1, int(math.ceil((r[3] + clearance) / step - 0.5)) - 1)
        if x1 < x0 or y1 < y0:
            return 0
        row = ((1 << (x1 - x0 + 1)) - 1) << x0
        mask = 0
        for y in range(y0, y1 + 1):
            mask |= row << (y * nx)
        return mask

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
                gap = {'west': r[0], 'east': width - r[2], 'south': r[1], 'north': depth - r[3]}[c['wall']]
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
            key = (r, o['yaw_deg'] if name in references else 0)
            if key in seen:
                continue
            seen.add(key)
            pp.append(o)
            rr.append(r)
            mm.append(m)
        poses.append(pp)
        rects.append(rr)
        masks.append(mm)

    def emergency():
        objects = list(fixed)
        for name in names:
            if name not in fixed_by_id:
                domain = packet['domains'].get(name, [])
                if domain:
                    objects.append(domain[0])
        return {'scene_id': spec['scene_id'], 'objects': objects}

    if any(not p for p in poses):
        return emergency()

    support = [[None for _ in names] for _ in names]
    neighbors = [[] for _ in names]
    for i in range(n):
        for j in range(i + 1, n):
            forward = [0] * len(poses[i])
            backward = [0] * len(poses[j])
            cs = relations.get((i, j), [])
            cr = relations.get((j, i), [])
            restrictive = False
            for a, o in enumerate(poses[i]):
                r = rects[i][a]
                for b, ref in enumerate(poses[j]):
                    rr = rects[j][b]
                    ok = not overlap(r, rr)
                    if ok:
                        ok = all(front_ok(o, r, ref, rr, c) for c in cs)
                    if ok:
                        ok = all(front_ok(ref, rr, o, r, c) for c in cr)
                    if ok:
                        forward[a] |= 1 << b
                        backward[b] |= 1 << a
                    else:
                        restrictive = True
            support[i][j], support[j][i] = forward, backward
            if restrictive:
                neighbors[i].append(j)
                neighbors[j].append(i)

    def propagate(state, changed):
        queue = deque(changed)
        pending = set(changed)
        while queue:
            j = queue.popleft()
            pending.discard(j)
            dj = state[j]
            for i in neighbors[j]:
                old = state[i]
                remaining = old
                revised = 0
                table = support[i][j]
                while remaining:
                    bit = remaining & -remaining
                    remaining -= bit
                    a = bit.bit_length() - 1
                    if table[a] & dj:
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
        return (((bits & not_right) << 1) | ((bits & not_left) >> 1) | (bits << W) | (bits >> W)) & board

    def navigation(blocked, path_needed=False):
        if blocked & endpoints:
            return -1, 0
        free = board ^ blocked
        frontier = sb
        visited = sb
        layers = []
        distance = 0
        while frontier:
            if path_needed:
                layers.append(frontier)
            if frontier & gb:
                path = 0
                if path_needed:
                    bit = gb
                    path = bit
                    for k in range(distance - 1, -1, -1):
                        options = expand(bit) & layers[k]
                        bit = options & -options
                        path |= bit
                return distance, path
            frontier = expand(frontier) & free & ~visited
            visited |= frontier
            distance += 1
        return -1, 0

    access_specs = spec.get('access_paths', [])
    fine_step = 0.1
    FW = int(math.ceil(width / fine_step))
    FH = int(math.ceil(depth / fine_step))
    fine_board = (1 << (FW * FH)) - 1
    fine_left = sum(1 << (y * FW) for y in range(FH))
    fine_right = fine_left << (FW - 1)
    fine_not_left = fine_board ^ fine_left
    fine_not_right = fine_board ^ fine_right
    fine_masks = {}
    access_data = []
    for access in access_specs:
        clearance = access.get('clearance', 0.15)
        if clearance not in fine_masks:
            fine_masks[clearance] = [[grid_mask(r, clearance, FW, FH, fine_step) for r in rs] for rs in rects]
        region = regions[access['from_region']]
        seeds = 0
        for y in range(FH):
            cy = (y + 0.5) * fine_step
            if not region['y'] - eps <= cy <= region['y'] + region['depth'] + eps:
                continue
            for x in range(FW):
                cx = (x + 0.5) * fine_step
                if region['x'] - eps <= cx <= region['x'] + region['width'] + eps and cx <= width and cy <= depth:
                    seeds |= 1 << (y * FW + x)
        target = index[access['to_object']]
        distance = access.get('interaction_distance', 0.8)
        target_masks = []
        for o in poses[target]:
            target_mask = 0
            x0 = max(0, int((o['x'] - distance) / fine_step) - 1)
            x1 = min(FW - 1, int((o['x'] + distance) / fine_step) + 1)
            y0 = max(0, int((o['y'] - distance) / fine_step) - 1)
            y1 = min(FH - 1, int((o['y'] + distance) / fine_step) + 1)
            for y in range(y0, y1 + 1):
                cy = (y + 0.5) * fine_step
                for x in range(x0, x1 + 1):
                    cx = (x + 0.5) * fine_step
                    if (cx - o['x']) ** 2 + (cy - o['y']) ** 2 <= distance * distance + eps:
                        target_mask |= 1 << (y * FW + x)
            target_masks.append(target_mask)
        access_data.append((clearance, seeds, target, target_masks))

    def accessible(chosen):
        for clearance, seeds, target, targets in access_data:
            blocked = 0
            table = fine_masks[clearance]
            for i, a in enumerate(chosen):
                blocked |= table[i][a]
            free = fine_board ^ blocked
            reached = seeds & free
            frontier = reached
            destination = targets[chosen[target]] & free
            if not reached or not destination:
                return False
            while frontier and not reached & destination:
                adjacent = (((frontier & fine_not_right) << 1) | ((frontier & fine_not_left) >> 1) | (frontier << FW) | (frontier >> FW)) & fine_board
                frontier = adjacent & free & ~reached
                reached |= frontier
            if not reached & destination:
                return False
        return True

    initial = [(1 << len(p)) - 1 for p in poses]
    if not propagate(initial, range(n)):
        return emergency()
    result = [None]
    best = [None, -1]
    complete = [None]
    work = [0]
    failed = set()
    limit = 26000

    def search(state):
        if work[0] >= limit:
            return False
        work[0] += 1
        signature = tuple(state)
        if signature in failed:
            return False
        mandatory = 0
        possible = 0
        remaining_count = 0
        variable = -1
        variable_key = None
        for i, domain in enumerate(state):
            bits = domain
            common = board
            union = 0
            while bits:
                bit = bits & -bits
                bits -= bit
                m = masks[i][bit.bit_length() - 1]
                common &= m
                union |= m
            mandatory |= common
            possible |= union
            size = domain.bit_count()
            if size > 1:
                remaining_count += 1
                key = (size, -len(neighbors[i]))
                if variable_key is None or key < variable_key:
                    variable_key, variable = key, i
        distance, path = navigation(mandatory, variable >= 0)
        if distance < 0:
            if len(failed) < 20000:
                failed.add(signature)
            return False
        if variable < 0:
            chosen = [d.bit_length() - 1 for d in state]
            if complete[0] is None:
                complete[0] = chosen
            if accessible(chosen):
                if distance > best[1]:
                    best[0], best[1] = chosen, distance
                if distance >= minimum:
                    result[0] = chosen
                    return True
            if len(failed) < 20000:
                failed.add(signature)
            return False
        if remaining_count <= 3 and distance < minimum:
            upper, unused = navigation(possible)
            if 0 <= upper < minimum:
                if len(failed) < 20000:
                    failed.add(signature)
                return False
        choices = []
        bits = state[variable]
        while bits:
            bit = bits & -bits
            bits -= bit
            a = bit.bit_length() - 1
            blocked = mandatory | masks[variable][a]
            new_distance, unused = navigation(blocked)
            if new_distance < 0:
                continue
            retained = 0
            for j in neighbors[variable]:
                retained += (support[variable][j][a] & state[j]).bit_count()
            hit = (masks[variable][a] & path).bit_count()
            score = (min(new_distance, minimum), hit if new_distance < minimum else -hit, retained)
            choices.append((score, bit))
        choices.sort(reverse=True)
        for score, bit in choices:
            if work[0] >= limit:
                break
            child = state[:]
            child[variable] = bit
            if propagate(child, [variable]) and search(child):
                return True
        if work[0] < limit and len(failed) < 20000:
            failed.add(signature)
        return False

    search(initial)
    chosen = result[0] if result[0] is not None else best[0]
    if chosen is None:
        chosen = complete[0]
    if chosen is None:
        return emergency()
    objects = list(fixed)
    objects.extend(poses[i][chosen[i]] for i, name in enumerate(names) if name not in fixed_by_id)
    return {'scene_id': spec['scene_id'], 'objects': objects}
