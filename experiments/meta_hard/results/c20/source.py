import math

def layout(packet):
    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    fixed = list(packet['fixed'])
    fixed_by_id = {o['id']: o for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    names = [o['id'] for o in spec['required_objects'] if o['id'] not in fixed_by_id]
    constraints = spec.get('constraints', [])
    regions = {r['id']: r for r in spec.get('regions', [])}
    access = spec.get('access_paths', [])
    minimum = packet['minimum_steps']
    eps = 1e-8

    def rectangle(o):
        a = assets[o['asset']]
        w, d = a['width'], a['depth']
        if o['yaw_deg'] % 180:
            w, d = d, w
        return (o['x'] - w / 2, o['y'] - d / 2,
                o['x'] + w / 2, o['y'] + d / 2)

    def overlaps(a, b):
        return (min(a[2], b[2]) - max(a[0], b[0]) > eps and
                min(a[3], b[3]) - max(a[1], b[1]) > eps)

    def front(subject, reference, limit):
        sr, rr = rectangle(subject), rectangle(reference)
        direction = reference['yaw_deg'] % 360
        fx, fy = {0: (0, 1), 90: (-1, 0),
                  180: (0, -1), 270: (1, 0)}[direction]
        dx = subject['x'] - reference['x']
        dy = subject['y'] - reference['y']
        hx = (sr[2] - sr[0] + rr[2] - rr[0]) / 2
        hy = (sr[3] - sr[1] + rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * hx - abs(fy) * hy
        lateral = abs(dx * fy - dy * fx)
        return (-eps <= gap <= limit + eps and
                lateral <= abs(fy) * hx + abs(fx) * hy + eps)

    clear_regions = []
    for c in constraints:
        if c['type'] == 'keep_clear':
            r = regions[c['region']]
            clear_regions.append((r['x'], r['y'], r['x'] + r['width'],
                                  r['y'] + r['depth']))
    relations = [c for c in constraints if c['type'] == 'in_front_of']
    reference_ids = {c['reference'] for c in relations}
    fixed_rects = [rectangle(o) for o in fixed]

    def unary(o):
        r = rectangle(o)
        if r[0] < -eps or r[1] < -eps or r[2] > width + eps or r[3] > depth + eps:
            return False
        if any(overlaps(r, q) for q in clear_regions):
            return False
        if any(overlaps(r, q) for q in fixed_rects):
            return False
        for c in constraints:
            if c['type'] == 'near_wall' and c['subject'] == o['id']:
                gap = {'west': r[0], 'south': r[1],
                       'east': width - r[2], 'north': depth - r[3]}[c['wall']]
                if gap < -eps or gap > c['max_gap'] + eps:
                    return False
        for c in relations:
            if c['subject'] == o['id'] and c['reference'] in fixed_by_id:
                if not front(o, fixed_by_id[c['reference']], c['max_gap']):
                    return False
            if c['reference'] == o['id'] and c['subject'] in fixed_by_id:
                if not front(fixed_by_id[c['subject']], o, c['max_gap']):
                    return False
            if c['subject'] == o['id'] and c['reference'] == o['id']:
                if not front(o, o, c['max_gap']):
                    return False
        return True

    gw, gh = int(width), int(depth)
    cell_count = gw * gh
    all_cells = (1 << cell_count) - 1
    left_edge = sum(1 << (y * gw) for y in range(gh))
    right_edge = left_edge << (gw - 1)
    not_left, not_right = all_cells ^ left_edge, all_cells ^ right_edge
    start = packet['start'][1] * gw + packet['start'][0]
    goal = packet['goal'][1] * gw + packet['goal'][0]
    start_bit, goal_bit = 1 << start, 1 << goal
    endpoints = start_bit | goal_bit

    def grid_mask(r, clearance, w, h, step):
        x0 = max(0, int(math.floor((r[0] - clearance) / step - 0.5)) + 1)
        x1 = min(w - 1, int(math.ceil((r[2] + clearance) / step - 0.5)) - 1)
        y0 = max(0, int(math.floor((r[1] - clearance) / step - 0.5)) + 1)
        y1 = min(h - 1, int(math.ceil((r[3] + clearance) / step - 0.5)) - 1)
        if x1 < x0 or y1 < y0:
            return 0
        row = ((1 << (x1 - x0 + 1)) - 1) << x0
        result = 0
        for y in range(y0, y1 + 1):
            result |= row << (y * w)
        return result

    fixed_mask = 0
    for r in fixed_rects:
        fixed_mask |= grid_mask(r, 0.15, gw, gh, 1.0)

    poses, rects, blocks = [], [], []
    for name in names:
        values, boxes, masks = [], [], []
        seen = set()
        for o in packet['domains'][name]:
            if o['id'] != name or o['asset'] != required[name] or not unary(o):
                continue
            r = rectangle(o)
            mask = grid_mask(r, 0.15, gw, gh, 1.0)
            if mask & endpoints:
                continue
            key = (r, o['yaw_deg'] % 360 if name in reference_ids else 0)
            if key in seen:
                continue
            seen.add(key)
            values.append(o)
            boxes.append(r)
            masks.append(mask)
        poses.append(values)
        rects.append(boxes)
        blocks.append(masks)
    n = len(names)
    index = {name: i for i, name in enumerate(names)}
    sizes = [len(v) for v in poses]
    domains = [(1 << k) - 1 for k in sizes]
    compatibility = [[None for j in range(n)] for i in range(n)]
    neighbors = [[] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            related = [c for c in relations
                       if {c['subject'], c['reference']} == {names[i], names[j]}]
            ij = [0] * sizes[i]
            ji = [0] * sizes[j]
            restrictive = False
            for a, o in enumerate(poses[i]):
                for b, q in enumerate(poses[j]):
                    good = not overlaps(rects[i][a], rects[j][b])
                    if good:
                        for c in related:
                            subject, reference = (o, q) if c['subject'] == names[i] else (q, o)
                            if not front(subject, reference, c['max_gap']):
                                good = False
                                break
                    if good:
                        ij[a] |= 1 << b
                        ji[b] |= 1 << a
                    else:
                        restrictive = True
            if restrictive:
                compatibility[i][j] = ij
                compatibility[j][i] = ji
                neighbors[i].append(j)
                neighbors[j].append(i)

    def propagate(ds, changed):
        queue = list(changed)
        head = 0
        queued = set(queue)
        while head < len(queue):
            j = queue[head]
            head += 1
            queued.discard(j)
            dj = ds[j]
            if not dj:
                return False
            for i in neighbors[j]:
                old = ds[i]
                rest, new = old, 0
                table = compatibility[i][j]
                while rest:
                    bit = rest & -rest
                    rest -= bit
                    a = bit.bit_length() - 1
                    if table[a] & dj:
                        new |= bit
                if new != old:
                    if not new:
                        return False
                    ds[i] = new
                    if i not in queued:
                        queued.add(i)
                        queue.append(i)
        return True

    distance_cache = {}

    def distance(blocked):
        if blocked in distance_cache:
            return distance_cache[blocked]
        if blocked & endpoints:
            return -1
        free = all_cells & ~blocked
        wave, visited, steps = start_bit, start_bit, 0
        while wave and not (wave & goal_bit):
            wave = (((wave & not_right) << 1) | ((wave & not_left) >> 1) |
                    (wave << gw) | (wave >> gw)) & free & ~visited
            visited |= wave
            steps += 1
        result = steps if wave else -1
        if len(distance_cache) < 18000:
            distance_cache[blocked] = result
        return result

    envelope_cache = [{} for i in range(n)]

    def envelope(i, ds):
        cache = envelope_cache[i]
        if ds in cache:
            return cache[ds]
        rest, certain, possible = ds, all_cells, 0
        while rest:
            bit = rest & -rest
            rest -= bit
            mask = blocks[i][bit.bit_length() - 1]
            certain &= mask
            possible |= mask
        result = (certain, possible)
        if len(cache) < 2500:
            cache[ds] = result
        return result

    fine_step = 0.1
    fw, fh = int(math.ceil(width / fine_step)), int(math.ceil(depth / fine_step))
    fine_all = (1 << (fw * fh)) - 1
    fine_left = sum(1 << (y * fw) for y in range(fh))
    fine_not_left = fine_all ^ fine_left
    fine_not_right = fine_all ^ (fine_left << (fw - 1))
    fine_masks = {}
    target_masks = {}
    region_masks = {}

    def fine_block(r, clearance):
        key = (r, clearance)
        if key not in fine_masks:
            fine_masks[key] = grid_mask(r, clearance, fw, fh, fine_step)
        return fine_masks[key]

    def region_mask(name):
        if name not in region_masks:
            r = regions[name]
            mask = 0
            for y in range(fh):
                cy = (y + 0.5) * fine_step
                if not r['y'] - eps <= cy <= r['y'] + r['depth'] + eps:
                    continue
                for x in range(fw):
                    cx = (x + 0.5) * fine_step
                    if r['x'] - eps <= cx <= r['x'] + r['width'] + eps:
                        mask |= 1 << (y * fw + x)
            region_masks[name] = mask
        return region_masks[name]

    def target_mask(r, reach):
        key = (r, reach)
        if key in target_masks:
            return target_masks[key]
        mask = 0
        for y in range(fh):
            cy = (y + 0.5) * fine_step
            dy = max(r[1] - cy, 0.0, cy - r[3])
            if dy > reach + eps:
                continue
            reach_x = math.sqrt(max(0.0, reach * reach - dy * dy))
            low = max(0, int(math.ceil((r[0] - reach_x) / fine_step - 0.5 - eps)))
            high = min(fw - 1, int(math.floor((r[2] + reach_x) / fine_step - 0.5 + eps)))
            if high >= low:
                mask |= (((1 << (high - low + 1)) - 1) << low) << (y * fw)
        target_masks[key] = mask
        return mask

    def accessible(chosen):
        if not access:
            return True
        objects = dict(fixed_by_id)
        selected_rects = list(fixed_rects)
        for i, a in enumerate(chosen):
            objects[names[i]] = poses[i][a]
            selected_rects.append(rects[i][a])
        occupancy = {}
        for path in access:
            clearance = path.get('clearance', 0.15)
            if clearance not in occupancy:
                blocked = 0
                for r in selected_rects:
                    blocked |= fine_block(r, clearance)
                occupancy[clearance] = blocked
            free = fine_all & ~occupancy[clearance]
            wave = region_mask(path['from_region']) & free
            target = target_mask(rectangle(objects[path['to_object']]),
                                 path.get('interaction_distance', 0.8)) & free
            if not wave or not target:
                return False
            visited = wave
            while wave and not (wave & target):
                wave = (((wave & fine_not_right) << 1) |
                        ((wave & fine_not_left) >> 1) |
                        (wave << fw) | (wave >> fw)) & free & ~visited
                visited |= wave
            if not wave:
                return False
        return True

    def chosen_values(ds):
        return [d.bit_length() - 1 for d in ds]

    def output(chosen):
        objects = list(fixed)
        objects.extend(poses[i][a] for i, a in enumerate(chosen))
        return {'scene_id': spec['scene_id'], 'objects': objects}

    def first_completion(ds, budget):
        budget[0] -= 1
        if budget[0] < 0:
            return None
        remaining = [i for i in range(n) if ds[i] & (ds[i] - 1)]
        if not remaining:
            return chosen_values(ds)
        i = min(remaining, key=lambda k: (ds[k].bit_count(), -len(neighbors[k])))
        rest = ds[i]
        while rest:
            bit = rest & -rest
            rest -= bit
            child = list(ds)
            child[i] = bit
            if propagate(child, [i]):
                result = first_completion(child, budget)
                if result is not None:
                    return result
        return None

    if any(not d for d in domains) or not propagate(domains, range(n)):
        return {'scene_id': spec['scene_id'], 'objects': fixed + [
            packet['domains'][name][0] for name in names]}
    baseline = first_completion(list(domains), [3000])
    best = [baseline, -1, False]
    if baseline is not None:
        blocked = fixed_mask
        for i, a in enumerate(baseline):
            blocked |= blocks[i][a]
        d = distance(blocked)
        ok = d >= 0 and accessible(baseline)
        best[:] = [baseline, d, ok]
        if ok and d >= minimum:
            return output(baseline)

    dead = set()
    visits = [0]

    def search(ds):
        visits[0] += 1
        if visits[0] > 65000:
            return None
        key = tuple(ds)
        if key in dead:
            return None
        certain, possible = fixed_mask, fixed_mask
        remaining = []
        for i, d in enumerate(ds):
            a, b = envelope(i, d)
            certain |= a
            possible |= b
            if d & (d - 1):
                remaining.append(i)
        lower = distance(certain)
        if lower < 0:
            return None
        if not remaining:
            chosen = chosen_values(ds)
            if accessible(chosen):
                if not best[2] or lower > best[1]:
                    best[:] = [chosen, lower, True]
                if lower >= minimum:
                    return chosen
            return None
        if best[2] and len(remaining) <= 3:
            upper = distance(possible)
            if 0 <= upper < minimum:
                return None
        i = min(remaining, key=lambda k: (ds[k].bit_count(),
                    -sum(1 for j in neighbors[k] if ds[j] & (ds[j] - 1)),
                    -len(neighbors[k])))
        candidates = []
        rest = ds[i]
        while rest:
            bit = rest & -rest
            rest -= bit
            a = bit.bit_length() - 1
            d = distance(certain | blocks[i][a])
            if d < 0:
                continue
            support = sum((compatibility[i][j][a] & ds[j]).bit_count()
                          for j in neighbors[i])
            score = (d >= minimum, -abs(d - minimum), support)
            candidates.append((score, bit))
        candidates.sort(reverse=True)
        for score, bit in candidates:
            child = list(ds)
            child[i] = bit
            if propagate(child, [i]):
                result = search(child)
                if result is not None:
                    return result
            if visits[0] > 65000:
                return None
        if len(dead) < 16000:
            dead.add(key)
        return None

    result = search(list(domains))
    if result is not None:
        return output(result)
    if best[0] is not None:
        return output(best[0])
    fallback = first_completion(list(domains), [12000])
    if fallback is not None:
        return output(fallback)
    return {'scene_id': spec['scene_id'], 'objects': fixed + [poses[i][0] for i in range(n)]}
