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
    names.extend(k for k in required if k not in fixed_by_id)
    index = {name: i for i, name in enumerate(names)}
    n = len(names)
    regions = {r['id']: r for r in spec.get('regions', [])}
    accesses = spec.get('access_paths', [])
    eps = 1e-8
    walls, relations, clear_regions = {}, [], []
    for c in spec.get('constraints', []):
        if c['type'] == 'near_wall':
            walls.setdefault(c['subject'], []).append(c)
        elif c['type'] == 'in_front_of':
            relations.append(c)
        elif c['type'] == 'keep_clear':
            r = regions[c['region']]
            clear_regions.append((r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth']))
    references = {c['reference'] for c in relations}

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

    def unary(o, r):
        if r[0] < -eps or r[1] < -eps or r[2] > width + eps or r[3] > depth + eps:
            return False
        if any(overlap(r, z) for z in clear_regions):
            return False
        for c in walls.get(o['id'], []):
            gap = {'west': r[0], 'east': width - r[2],
                   'south': r[1], 'north': depth - r[3]}[c['wall']]
            if gap < -eps or gap > c['max_gap'] + eps:
                return False
        return True

    def front(o, r, q, s, limit):
        fx, fy = {0: (0, 1), 90: (-1, 0),
                  180: (0, -1), 270: (1, 0)}[q['yaw_deg'] % 360]
        dx, dy = o['x'] - q['x'], o['y'] - q['y']
        wx = (r[2] - r[0] + s[2] - s[0]) / 2
        wy = (r[3] - r[1] + s[3] - s[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * wx - abs(fy) * wy
        lateral = abs(fy) * wx + abs(fx) * wy
        return -eps <= gap <= limit + eps and abs(dx * fy - dy * fx) <= lateral + eps

    def grid(w, h):
        whole = (1 << (w * h)) - 1
        left = sum(1 << (y * w) for y in range(h))
        return (w, h, whole, whole ^ left, whole ^ (left << (w - 1)))

    gw, gh = int(width), int(depth)
    navigation = grid(gw, gh)
    sb = 1 << (int(packet['start'][0]) + gw * int(packet['start'][1]))
    gb = 1 << (int(packet['goal'][0]) + gw * int(packet['goal'][1]))
    endpoints = sb | gb
    minimum = packet['minimum_steps']

    def rectangle_mask(r, clearance, step, g):
        w, h = g[0], g[1]
        lo = max(0, int(math.floor((r[0] - clearance) / step - 0.5)) + 1)
        hi = min(w - 1, int(math.ceil((r[2] + clearance) / step - 0.5)) - 1)
        bottom = max(0, int(math.floor((r[1] - clearance) / step - 0.5)) + 1)
        top = min(h - 1, int(math.ceil((r[3] + clearance) / step - 0.5)) - 1)
        if lo > hi or bottom > top:
            return 0
        row = ((1 << (hi - lo + 1)) - 1) << lo
        result = 0
        for y in range(bottom, top + 1):
            result |= row << (y * w)
        return result

    poses, rects, masks = [], [], []
    for name in names:
        source = [fixed_by_id[name]] if name in fixed_by_id else packet['domains'][name]
        pp, rr, mm, seen = [], [], [], set()
        for o in source:
            if o['id'] != name or o['asset'] != required.get(name, o['asset']):
                continue
            r = rectangle(o)
            if not unary(o, r):
                continue
            m = rectangle_mask(r, 0.15, 1.0, navigation)
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

    pair_rel = {}
    for c in relations:
        a, b = index[c['subject']], index[c['reference']]
        pair_rel.setdefault((min(a, b), max(a, b)), []).append((a, b, c['max_gap']))
    for i in range(n):
        if (i, i) in pair_rel:
            keep = []
            for a, o in enumerate(poses[i]):
                if all(front(o, rects[i][a], o, rects[i][a], limit)
                       for sub, ref, limit in pair_rel[(i, i)]):
                    keep.append(a)
            poses[i] = [poses[i][a] for a in keep]
            rects[i] = [rects[i][a] for a in keep]
            masks[i] = [masks[i][a] for a in keep]

    support = [[None] * n for i in range(n)]
    neighbors = [[] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            si, sj = [0] * len(poses[i]), [0] * len(poses[j])
            rel = pair_rel.get((i, j), [])
            full_j = (1 << len(poses[j])) - 1
            for a, o in enumerate(poses[i]):
                r = rects[i][a]
                for b, q in enumerate(poses[j]):
                    s = rects[j][b]
                    if overlap(r, s):
                        continue
                    good = True
                    for sub, ref, limit in rel:
                        if sub == i:
                            good = front(o, r, q, s, limit)
                        else:
                            good = front(q, s, o, r, limit)
                        if not good:
                            break
                    if good:
                        si[a] |= 1 << b
                        sj[b] |= 1 << a
            if any(bits != full_j for bits in si):
                support[i][j], support[j][i] = si, sj
                neighbors[i].append(j)
                neighbors[j].append(i)

    def propagate(state, arcs):
        queue = deque(arcs)
        pending = set(queue)
        while queue:
            i, j = queue.popleft()
            pending.discard((i, j))
            bits, kept = state[i], 0
            table = support[i][j]
            while bits:
                bit = bits & -bits
                bits -= bit
                if table[bit.bit_length() - 1] & state[j]:
                    kept |= bit
            if kept != state[i]:
                if not kept:
                    return False
                state[i] = kept
                for k in neighbors[i]:
                    arc = (k, i)
                    if k != j and arc not in pending:
                        pending.add(arc)
                        queue.append(arc)
        return True

    distance_cache = {}

    def distance(blocked):
        cached = distance_cache.get(blocked)
        if cached is not None:
            return cached
        result = -1
        if not blocked & endpoints:
            w, h, whole, no_left, no_right = navigation
            free = whole & ~blocked
            frontier, visited, d = sb, sb, 0
            while frontier:
                if frontier & gb:
                    result = d
                    break
                frontier = (((frontier & no_left) >> 1) |
                            ((frontier & no_right) << 1) |
                            (frontier << w) | (frontier >> w)) & free & ~visited
                visited |= frontier
                d += 1
        if len(distance_cache) >= 40000:
            distance_cache.clear()
        distance_cache[blocked] = result
        return result

    aggregate_cache = [{} for i in range(n)]

    def forced_and_possible(state):
        forced, possible = 0, 0
        for i, domain in enumerate(state):
            cache = aggregate_cache[i]
            pair = cache.get(domain)
            if pair is None:
                intersection, union = navigation[2], 0
                bits = domain
                while bits:
                    bit = bits & -bits
                    bits -= bit
                    m = masks[i][bit.bit_length() - 1]
                    intersection &= m
                    union |= m
                pair = (intersection, union)
                if len(cache) < 4096:
                    cache[domain] = pair
            forced |= pair[0]
            possible |= pair[1]
        return forced, possible

    fine_step = 0.1
    fine = grid(int(math.ceil(width / fine_step)), int(math.ceil(depth / fine_step)))
    fine_masks, target_masks = {}, {}
    access_failures = set()
    sources = []
    for access in accesses:
        r = regions[access['from_region']]
        rr = (r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth'])
        sources.append(rectangle_mask(rr, 0.0, fine_step, fine))

    def fine_object_mask(i, a, clearance):
        key = (i, a, clearance)
        value = fine_masks.get(key)
        if value is None:
            value = rectangle_mask(rects[i][a], clearance, fine_step, fine)
            fine_masks[key] = value
        return value

    def proximity_mask(r, radius):
        w, h = fine[0], fine[1]
        result = 0
        for y in range(h):
            cy = (y + 0.5) * fine_step
            if cy > depth:
                continue
            dy = max(r[1] - cy, 0.0, cy - r[3])
            if dy > radius:
                continue
            extension = math.sqrt(max(0.0, radius * radius - dy * dy))
            lo = max(0, int(math.ceil((r[0] - extension) / fine_step - 0.5 - 1e-9)))
            hi = min(w - 1, int(math.floor((r[2] + extension) / fine_step - 0.5 + 1e-9)))
            if lo <= hi:
                result |= ((1 << (hi - lo + 1)) - 1) << (lo + y * w)
        return result

    def access_ok(chosen):
        occupancy = {}
        w, h, whole, no_left, no_right = fine
        for k, access in enumerate(accesses):
            clearance = access.get('clearance', 0.15)
            if clearance not in occupancy:
                blocked = 0
                for i, a in enumerate(chosen):
                    blocked |= fine_object_mask(i, a, clearance)
                occupancy[clearance] = blocked
            blocked = occupancy[clearance]
            i = index[access['to_object']]
            a = chosen[i]
            cache_key = (k, blocked, a)
            if cache_key in access_failures:
                return False
            radius = access.get('interaction_distance', 0.8)
            target_key = (i, a, radius)
            if target_key not in target_masks:
                target_masks[target_key] = proximity_mask(rects[i][a], radius)
            free = whole & ~blocked
            target = target_masks[target_key] & free
            frontier = sources[k] & free
            visited = frontier
            while frontier and not frontier & target:
                frontier = (((frontier & no_left) >> 1) |
                            ((frontier & no_right) << 1) |
                            (frontier << w) | (frontier >> w)) & free & ~visited
                visited |= frontier
            if not frontier & target:
                if len(access_failures) < 5000:
                    access_failures.add(cache_key)
                return False
        return True

    initial = [(1 << len(pp)) - 1 for pp in poses]
    budget = [0]
    fallback = [None, -1]
    failed_states = set()

    def search(state):
        budget[0] += 1
        if budget[0] > 50000:
            return None
        key = tuple(state)
        if key in failed_states:
            return None
        forced, possible = forced_and_possible(state)
        low = distance(forced)
        if low < 0:
            return None
        unresolved = [i for i in range(n) if state[i] & (state[i] - 1)]
        if not unresolved:
            chosen = [bits.bit_length() - 1 for bits in state]
            if low >= minimum:
                if access_ok(chosen):
                    return chosen
            elif low > fallback[1] and access_ok(chosen):
                fallback[0], fallback[1] = chosen, low
            return None
        if low < minimum:
            high = distance(possible)
            if 0 <= high < minimum:
                return None
        variable = min(unresolved, key=lambda i: (
            state[i].bit_count(),
            -sum(1 for j in unresolved if j != i and
                 (min(i, j), max(i, j)) in pair_rel),
            -len(neighbors[i])))
        values = []
        bits = state[variable]
        while bits:
            bit = bits & -bits
            bits -= bit
            a = bit.bit_length() - 1
            d = distance(forced | masks[variable][a])
            if d < 0:
                continue
            freedom = 0
            for j in neighbors[variable]:
                if state[j] & (state[j] - 1):
                    freedom += (support[variable][j][a] & state[j]).bit_count() - state[j].bit_count()
            o = poses[variable][a]
            values.append((min(d, minimum), freedom, o['x'], o['y'], o['yaw_deg'], a))
        values.sort(reverse=True)
        for score, freedom, x, y, yaw, a in values:
            child = state[:]
            child[variable] = 1 << a
            if propagate(child, [(j, variable) for j in neighbors[variable]]):
                answer = search(child)
                if answer is not None:
                    return answer
            if budget[0] > 50000:
                break
        if budget[0] <= 50000 and len(failed_states) < 30000:
            failed_states.add(key)
        return None

    chosen = None
    if all(initial) and propagate(initial, [(i, j) for i in range(n) for j in neighbors[i]]):
        chosen = search(initial)
    if chosen is None:
        chosen = fallback[0]
    objects = list(fixed)
    for i, name in enumerate(names):
        if name in fixed_by_id:
            continue
        if chosen is not None:
            objects.append(poses[i][chosen[i]])
        elif poses[i]:
            objects.append(poses[i][0])
        else:
            objects.append(packet['domains'][name][0])
    return {'scene_id': spec['scene_id'], 'objects': objects}
