import math
from collections import deque


def layout(packet):
    eps = 1e-7
    spec = packet['spec']
    assets = spec['assets']
    required = spec['required_objects']
    fixed = {p['id']: p for p in packet['fixed']}
    names = [r['id'] for r in required]
    index = {name: i for i, name in enumerate(names)}
    n = len(names)
    requirements = packet['requirements']
    poses = []
    boxes = []
    initial = []
    nx = int(2 * packet['width'] - 1)
    ny = int(2 * packet['depth'] - 1)
    cells = nx * ny
    universe = (1 << cells) - 1
    left_edge = sum(1 << (y * nx) for y in range(ny))
    right_edge = left_edge << (nx - 1)
    sx, sy = (int(2 * v) for v in packet['start'])
    gx, gy = (int(2 * v) for v in packet['goal'])
    start_bit = 1 << (sy * nx + sx)
    goal_bit = 1 << (gy * nx + gx)
    endpoints = start_bit | goal_bit
    blocked_cache = {}
    blocked = []

    def envelope(p):
        a = assets[p['asset']]
        w, d = a['width'], a['depth']
        if p['yaw_deg'] % 180:
            w, d = d, w
        return (p['x'] - w / 2, p['y'] - d / 2, p['z'],
                p['x'] + w / 2, p['y'] + d / 2,
                p['z'] + a['height'])

    def occupancy(b):
        if b in blocked_cache:
            return blocked_cache[b]
        mask = 0
        if b[2] < 1.8 - eps and b[5] > eps:
            x0 = max(0, int(math.floor(2 * (b[0] - .7))) + 1)
            x1 = min(nx - 1, int(math.ceil(2 * (b[3] - .3))) - 1)
            y0 = max(0, int(math.floor(2 * (b[1] - .7))) + 1)
            y1 = min(ny - 1, int(math.ceil(2 * (b[4] - .3))) - 1)
            if x0 <= x1 and y0 <= y1:
                row = ((1 << (x1 - x0 + 1)) - 1) << x0
                for y in range(y0, y1 + 1):
                    mask |= row << (y * nx)
        blocked_cache[b] = mask
        return mask

    for r in required:
        name = r['id']
        options = [fixed[name]] if name in fixed else packet['domains'][name]
        poses.append(options)
        bs = [envelope(p) for p in options]
        boxes.append(bs)
        blocks = [occupancy(b) for b in bs]
        blocked.append(blocks)
        mask = 0
        rule = requirements.get(name)
        for k, p in enumerate(options):
            b = bs[k]
            valid = p['id'] == name and p['asset'] == r['asset']
            valid = valid and b[0] >= -eps and b[1] >= -eps
            valid = valid and b[3] <= spec['room']['width'] + eps
            valid = valid and b[4] <= spec['room']['depth'] + eps
            valid = valid and b[2] >= 0 and b[5] <= spec['room']['height'] + eps
            if rule is not None:
                room = packet['rooms'][rule['room']]
                valid = valid and room[0] - eps <= b[0] and room[1] - eps <= b[1]
                valid = valid and b[3] <= room[2] + eps and b[4] <= room[3] + eps
                valid = valid and rule['height'][0] - eps <= b[2] <= rule['height'][1] + eps
                if rule['support'] == 'floor':
                    valid = valid and abs(b[2]) < eps
            if valid and not blocks[k] & endpoints:
                mask |= 1 << k
        initial.append(mask)

    relations = {}
    for r in packet['distances']:
        i, j = index[r['a']], index[r['b']]
        relations.setdefault((min(i, j), max(i, j)), []).append(
            (max(0., r['min'] - eps) ** 2, (r['max'] + eps) ** 2))
    parents = []
    for name in names:
        rule = requirements.get(name)
        parents.append(index.get(rule['support'], -1) if rule else -1)

    def supports(child, parent):
        return (abs(child[2] - parent[5]) <= eps
                and parent[0] + .05 - eps <= child[0]
                and parent[1] + .05 - eps <= child[1]
                and child[3] <= parent[3] - .05 + eps
                and child[4] <= parent[4] - .05 + eps)

    compatibility = [{} for i in range(n)]
    neighbors = [[] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            limits = relations.get((i, j), ())
            ij = [0] * len(poses[i])
            ji = [0] * len(poses[j])
            cache = {}
            nontrivial = False
            ai = initial[i]
            while ai:
                ib = ai & -ai
                ai -= ib
                a = ib.bit_length() - 1
                ba = boxes[i][a]
                bj = initial[j]
                while bj:
                    jb = bj & -bj
                    bj -= jb
                    b = jb.bit_length() - 1
                    bb = boxes[j][b]
                    key = (ba, bb)
                    if key in cache:
                        good = cache[key]
                    else:
                        good = not (min(ba[3], bb[3]) - max(ba[0], bb[0]) > eps
                                    and min(ba[4], bb[4]) - max(ba[1], bb[1]) > eps
                                    and min(ba[5], bb[5]) - max(ba[2], bb[2]) > eps)
                        if good and parents[i] == j:
                            good = supports(ba, bb)
                        if good and parents[j] == i:
                            good = supports(bb, ba)
                        if good and limits:
                            distance2 = sum(((ba[t] + ba[t + 3] - bb[t] - bb[t + 3]) * .5) ** 2 for t in range(3))
                            good = all(lo <= distance2 <= hi for lo, hi in limits)
                        cache[key] = good
                    if good:
                        ij[a] |= jb
                        ji[b] |= ib
                    else:
                        nontrivial = True
            if nontrivial:
                compatibility[i][j] = ij
                compatibility[j][i] = ji
                neighbors[i].append(j)
                neighbors[j].append(i)

    visible = [(index[name], int(math.ceil(27 * (limits[0] - eps))),
                int(math.floor(27 * (limits[1] + eps))))
               for name, limits in packet['visibility'].items()]
    visible_ids = {v[0] for v in visible}
    camera = packet['view']
    origin = camera['position']
    forward = [camera['target'][t] - origin[t] for t in range(3)]
    norm = math.sqrt(sum(v * v for v in forward))
    forward = [v / norm for v in forward] if norm else [0., 1., 0.]
    horizontal = math.hypot(forward[0], forward[1])
    right = ([forward[1] / horizontal, -forward[0] / horizontal, 0.]
             if horizontal > 1e-12 else [1., 0., 0.])
    up = [right[1] * forward[2], -right[0] * forward[2],
          right[0] * forward[1] - right[1] * forward[0]]
    tangent = math.tan(math.radians(camera['fov'] * .5))
    rays = {}
    occlusions = {}
    work = [0, 0, 0, False]

    def samples(b):
        if b in rays:
            return rays[b]
        result = []
        mask = 0
        for fx in (.15, .5, .85):
            for fy in (.15, .5, .85):
                for fz in (.15, .5, .85):
                    fractions = (fx, fy, fz)
                    delta = tuple(b[t] + fractions[t] * (b[t + 3] - b[t]) - origin[t] for t in range(3))
                    depth = sum(delta[t] * forward[t] for t in range(3))
                    framed = depth > 0 and all(abs(sum(delta[t] * axis[t] for t in range(3))) <= depth * tangent for axis in (right, up))
                    if framed:
                        bit = 1 << len(result)
                        mask |= bit
                        result.append((bit, delta))
        rays[b] = (mask, result)
        return rays[b]

    def occlusion(target, other):
        work[2] += 1
        if work[2] > 350000:
            work[3] = True
            return 0
        key = (target, other)
        if key in occlusions:
            return occlusions[key]
        mask = 0
        for bit, delta in samples(target)[1]:
            work[1] += 1
            if work[1] > 650000:
                work[3] = True
                return 0
            lo, hi = 0., 1. - 1e-6
            hit = True
            for t in range(3):
                d = delta[t]
                if abs(d) < 1e-10:
                    if not other[t] <= origin[t] <= other[t + 3]:
                        hit = False
                        break
                else:
                    a = (other[t] - origin[t]) / d
                    c = (other[t + 3] - origin[t]) / d
                    if a > c:
                        a, c = c, a
                    if a > lo:
                        lo = a
                    if c < hi:
                        hi = c
                    if lo > hi:
                        hit = False
                        break
            if hit:
                mask |= bit
        occlusions[key] = mask
        return mask

    for i, minimum, maximum in visible:
        keep = 0
        mask = initial[i]
        while mask:
            bit = mask & -mask
            mask -= bit
            k = bit.bit_length() - 1
            b = boxes[i][k]
            framed = samples(b)[0]
            for name, p in fixed.items():
                if name != names[i]:
                    framed &= ~occlusion(b, envelope(p))
            if framed.bit_count() >= minimum:
                keep |= bit
        initial[i] = keep

    navigation_cache = {}

    def route(mask):
        if mask in navigation_cache:
            return navigation_cache[mask]
        if mask & endpoints:
            return False
        free = universe & ~mask
        seen = start_bit
        frontier = start_bit
        while frontier:
            if frontier & goal_bit:
                if len(navigation_cache) < 12000:
                    navigation_cache[mask] = True
                return True
            frontier = (((frontier & ~right_edge) << 1)
                        | ((frontier & ~left_edge) >> 1)
                        | (frontier << nx) | (frontier >> nx)) & free & ~seen
            seen |= frontier
        if len(navigation_cache) < 12000:
            navigation_cache[mask] = False
        return False

    def visibility_filter(dom):
        changed = []
        for target_i, minimum, maximum in visible:
            target_domain = dom[target_i]
            if target_domain & (target_domain - 1):
                continue
            target = boxes[target_i][target_domain.bit_length() - 1]
            base = samples(target)[0]
            ids = []
            options = []
            certain = []
            possible = []
            for j in range(n):
                if j == target_i:
                    continue
                ids.append(j)
                entries = []
                must, may = base, 0
                mask = dom[j]
                while mask:
                    bit = mask & -mask
                    mask -= bit
                    k = bit.bit_length() - 1
                    hit = occlusion(target, boxes[j][k])
                    if work[3]:
                        return None
                    entries.append((bit, hit))
                    must &= hit
                    may |= hit
                options.append(entries)
                certain.append(must)
                possible.append(may)
            count = len(ids)
            cp, pp = [0], [0]
            for k in range(count):
                cp.append(cp[-1] | certain[k])
                pp.append(pp[-1] | possible[k])
            if (base & ~cp[-1]).bit_count() < minimum:
                return None
            if (base & ~pp[-1]).bit_count() > maximum:
                return None
            cs, ps = 0, 0
            for k in range(count - 1, -1, -1):
                must_other = cp[k] | cs
                may_other = pp[k] | ps
                keep = 0
                for bit, hit in options[k]:
                    upper = (base & ~(must_other | hit)).bit_count()
                    lower = (base & ~(may_other | hit)).bit_count()
                    if upper >= minimum and lower <= maximum:
                        keep |= bit
                j = ids[k]
                if not keep:
                    return None
                if keep != dom[j]:
                    dom[j] = keep
                    changed.append(j)
                cs |= certain[k]
                ps |= possible[k]
        return changed

    def propagate(dom, changed):
        if any(mask == 0 for mask in dom) or work[3]:
            return False
        queue = deque((i, j) for j in changed for i in neighbors[j])
        while True:
            while queue:
                i, j = queue.popleft()
                work[0] += 1
                if work[0] > 350000:
                    work[3] = True
                    return False
                table = compatibility[i][j]
                mask = dom[i]
                keep = 0
                while mask:
                    bit = mask & -mask
                    mask -= bit
                    if table[bit.bit_length() - 1] & dom[j]:
                        keep |= bit
                if not keep:
                    return False
                if keep != dom[i]:
                    dom[i] = keep
                    queue.extend((k, i) for k in neighbors[i] if k != j)
            changes = visibility_filter(dom)
            if changes is None:
                return False
            if changes:
                queue.extend((k, j) for j in changes for k in neighbors[j])
                continue
            mandatory = 0
            for i in range(n):
                mask = dom[i]
                occupied = universe
                while mask and occupied:
                    bit = mask & -mask
                    mask -= bit
                    occupied &= blocked[i][bit.bit_length() - 1]
                mandatory |= occupied
            return route(mandatory)

    progress = [0, None, -1]

    def search(dom, changed):
        progress[0] += 1
        if progress[0] > 10000 or work[3]:
            return None
        if not propagate(dom, changed):
            return None
        singles = sum(mask.bit_count() == 1 for mask in dom)
        if singles > progress[2]:
            progress[1] = dom[:]
            progress[2] = singles
        if singles == n:
            return dom
        choices = [i for i in range(n) if dom[i] & (dom[i] - 1)]
        i = min(choices, key=lambda j: (dom[j].bit_count(),
                    -(2 * (j in visible_ids) + sum(dom[k].bit_count() > 1 for k in neighbors[j]))))
        ranked = []
        mask = dom[i]
        while mask:
            bit = mask & -mask
            mask -= bit
            k = bit.bit_length() - 1
            freedom = sum((compatibility[i][j][k] & dom[j]).bit_count() / dom[j].bit_count()
                          for j in neighbors[i] if dom[j].bit_count() > 1)
            ranked.append((-freedom, blocked[i][k].bit_count(), k, bit))
        ranked.sort()
        for _, _, k, bit in ranked:
            child = dom[:]
            child[i] = bit
            solution = search(child, [i])
            if solution is not None:
                return solution
            if work[3] or progress[0] > 10000:
                break
        return None

    solution = search(initial[:], list(range(n)))
    if solution is None:
        solution = progress[1] if progress[1] is not None else initial
    selected = []
    for i, name in enumerate(names):
        if name in fixed:
            selected.append(fixed[name])
        else:
            mask = solution[i]
            k = (mask & -mask).bit_length() - 1 if mask else 0
            selected.append(poses[i][k])
    return {'state': {'scene_id': spec['scene_id'],
                      'objects': [{key: p[key] for key in ('id', 'asset', 'x', 'y', 'yaw_deg')}
                                  for p in selected]},
            'elevations': {p['id']: p['z'] for p in selected}}
