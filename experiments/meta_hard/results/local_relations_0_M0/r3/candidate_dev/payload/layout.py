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
        w, h = a['width'], a['depth']
        if o['yaw_deg'] % 180:
            w, h = h, w
        return (o['x'] - w / 2, o['y'] - h / 2,
                o['x'] + w / 2, o['y'] + h / 2)

    def overlaps(a, b):
        return (min(a[2], b[2]) - max(a[0], b[0]) > eps and
                min(a[3], b[3]) - max(a[1], b[1]) > eps)

    def front(o, q, limit):
        a, b = rectangle(o), rectangle(q)
        fx, fy = {0: (0, 1), 90: (-1, 0),
                  180: (0, -1), 270: (1, 0)}[q['yaw_deg'] % 360]
        dx, dy = o['x'] - q['x'], o['y'] - q['y']
        hx = (a[2] - a[0] + b[2] - b[0]) / 2
        hy = (a[3] - a[1] + b[3] - b[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * hx - abs(fy) * hy
        return (-eps <= gap <= limit + eps and
                abs(dx * fy - dy * fx) <= abs(fy) * hx + abs(fx) * hy + eps)

    clear = []
    for c in constraints:
        if c['type'] == 'keep_clear':
            r = regions[c['region']]
            clear.append((r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth']))
    relations = [c for c in constraints if c['type'] == 'in_front_of']
    reference_ids = {c['reference'] for c in relations}
    fixed_rects = [rectangle(o) for o in fixed]

    def unary(o):
        r = rectangle(o)
        if r[0] < -eps or r[1] < -eps or r[2] > width + eps or r[3] > depth + eps:
            return False
        if any(overlaps(r, q) for q in clear):
            return False
        if any(overlaps(r, q) for q in fixed_rects):
            return False
        for c in constraints:
            if c['type'] == 'near_wall' and c['subject'] == o['id']:
                gap = {'west': r[0], 'south': r[1],
                       'east': width - r[2], 'north': depth - r[3]}[c['wall']]
                if not -eps <= gap <= c['max_gap'] + eps:
                    return False
        for c in relations:
            if c['subject'] == o['id'] and c['reference'] in fixed_by_id:
                if not front(o, fixed_by_id[c['reference']], c['max_gap']):
                    return False
            if c['reference'] == o['id'] and c['subject'] in fixed_by_id:
                if not front(fixed_by_id[c['subject']], o, c['max_gap']):
                    return False
            if c['subject'] == c['reference'] == o['id']:
                if not front(o, o, c['max_gap']):
                    return False
        return True

    def grid_mask(r, clearance, w, h, step):
        x0 = max(0, int(math.floor((r[0] - clearance) / step - 0.5)) + 1)
        x1 = min(w - 1, int(math.ceil((r[2] + clearance) / step - 0.5)) - 1)
        y0 = max(0, int(math.floor((r[1] - clearance) / step - 0.5)) + 1)
        y1 = min(h - 1, int(math.ceil((r[3] + clearance) / step - 0.5)) - 1)
        if x0 > x1 or y0 > y1:
            return 0
        row = ((1 << (x1 - x0 + 1)) - 1) << x0
        mask = 0
        for y in range(y0, y1 + 1):
            mask |= row << (y * w)
        return mask

    gw, gh = int(width), int(depth)
    all_cells = (1 << (gw * gh)) - 1
    left = sum(1 << (y * gw) for y in range(gh))
    not_left = all_cells ^ left
    not_right = all_cells ^ (left << (gw - 1))
    start = 1 << (packet['start'][1] * gw + packet['start'][0])
    goal = 1 << (packet['goal'][1] * gw + packet['goal'][0])
    endpoints = start | goal
    fixed_mask = 0
    for r in fixed_rects:
        fixed_mask |= grid_mask(r, 0.15, gw, gh, 1.0)

    poses, rects, blocks = [], [], []
    for name in names:
        values, boxes, masks, seen = [], [], [], set()
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
    sizes = [len(v) for v in poses]
    domains = [(1 << k) - 1 for k in sizes]
    compatibility = [[None for j in range(n)] for i in range(n)]
    neighbors = [[] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            related = [c for c in relations if
                       (c['subject'] == names[i] and c['reference'] == names[j]) or
                       (c['subject'] == names[j] and c['reference'] == names[i])]
            ij, ji = [0] * sizes[i], [0] * sizes[j]
            restrictive = False
            for a, o in enumerate(poses[i]):
                for b, q in enumerate(poses[j]):
                    good = not overlaps(rects[i][a], rects[j][b])
                    if good:
                        for c in related:
                            s, r = (o, q) if c['subject'] == names[i] else (q, o)
                            if not front(s, r, c['max_gap']):
                                good = False
                                break
                    if good:
                        ij[a] |= 1 << b
                        ji[b] |= 1 << a
                    else:
                        restrictive = True
            if restrictive:
                compatibility[i][j], compatibility[j][i] = ij, ji
                neighbors[i].append(j)
                neighbors[j].append(i)

    def propagate(ds, changed):
        queue = list(changed)
        queued = set(queue)
        head = 0
        while head < len(queue):
            j = queue[head]
            head += 1
            queued.discard(j)
            if not ds[j]:
                return False
            for i in neighbors[j]:
                old, new = ds[i], 0
                rest = old
                table = compatibility[i][j]
                while rest:
                    bit = rest & -rest
                    rest -= bit
                    if table[bit.bit_length() - 1] & ds[j]:
                        new |= bit
                if new != old:
                    if not new:
                        return False
                    ds[i] = new
                    if i not in queued:
                        queued.add(i)
                        queue.append(i)
        return True

    navigation_cache = {}

    def adjacent(wave):
        return (((wave & not_right) << 1) | ((wave & not_left) >> 1) |
                (wave << gw) | (wave >> gw)) & all_cells

    def navigation(blocked):
        if blocked in navigation_cache:
            return navigation_cache[blocked]
        if blocked & endpoints:
            return (-1, 0)
        free = all_cells & ~blocked
        wave, visited = start, start
        levels = [wave]
        steps = 0
        while wave and not wave & goal:
            wave = adjacent(wave) & free & ~visited
            visited |= wave
            levels.append(wave)
            steps += 1
        if not wave:
            result = (-1, 0)
        else:
            path = 0
            if steps < minimum:
                current = goal
                path = current
                for k in range(steps - 1, -1, -1):
                    options = adjacent(current) & levels[k]
                    current = options & -options
                    path |= current
            result = (steps, path)
        if len(navigation_cache) < 16000:
            navigation_cache[blocked] = result
        return result

    envelope_cache = [{} for i in range(n)]

    def envelope(i, domain):
        cache = envelope_cache[i]
        if domain in cache:
            return cache[domain]
        rest, certain, possible = domain, all_cells, 0
        while rest:
            bit = rest & -rest
            rest -= bit
            mask = blocks[i][bit.bit_length() - 1]
            certain &= mask
            possible |= mask
        result = (certain, possible)
        if len(cache) < 1500:
            cache[domain] = result
        return result

    step = 0.1
    fw, fh = int(math.ceil(width / step)), int(math.ceil(depth / step))
    fine_all = (1 << (fw * fh)) - 1 if access else 0
    fine_left = sum(1 << (y * fw) for y in range(fh)) if access else 0
    fine_not_left = fine_all ^ fine_left
    fine_not_right = fine_all ^ (fine_left << (fw - 1))
    fine_masks, region_masks, target_masks = {}, {}, {}

    def fine_block(r, clearance):
        key = (r, clearance)
        if key not in fine_masks:
            fine_masks[key] = grid_mask(r, clearance, fw, fh, step)
        return fine_masks[key]

    def region_mask(name):
        if name not in region_masks:
            r = regions[name]
            mask = 0
            for y in range(fh):
                cy = (y + 0.5) * step
                if r['y'] - eps <= cy <= r['y'] + r['depth'] + eps:
                    for x in range(fw):
                        cx = (x + 0.5) * step
                        if r['x'] - eps <= cx <= r['x'] + r['width'] + eps:
                            mask |= 1 << (y * fw + x)
            region_masks[name] = mask
        return region_masks[name]

    def target_mask(r, reach):
        key = (r, reach)
        if key not in target_masks:
            mask = 0
            for y in range(fh):
                cy = (y + 0.5) * step
                dy = max(r[1] - cy, 0.0, cy - r[3])
                if dy > reach + eps:
                    continue
                dx = math.sqrt(max(0.0, reach * reach - dy * dy))
                lo = max(0, int(math.ceil((r[0] - dx) / step - 0.5 - eps)))
                hi = min(fw - 1, int(math.floor((r[2] + dx) / step - 0.5 + eps)))
                if hi >= lo:
                    mask |= ((1 << (hi - lo + 1)) - 1) << (y * fw + lo)
            target_masks[key] = mask
        return target_masks[key]

    def accessible(chosen):
        if not access:
            return True
        boxes = dict((o['id'], r) for o, r in zip(fixed, fixed_rects))
        selected = list(fixed_rects)
        for i, a in enumerate(chosen):
            boxes[names[i]] = rects[i][a]
            selected.append(rects[i][a])
        occupancy = {}
        for path in access:
            clearance = path.get('clearance', 0.15)
            if clearance not in occupancy:
                blocked = 0
                for r in selected:
                    blocked |= fine_block(r, clearance)
                occupancy[clearance] = blocked
            free = fine_all & ~occupancy[clearance]
            wave = region_mask(path['from_region']) & free
            target = target_mask(boxes[path['to_object']],
                                 path.get('interaction_distance', 0.8)) & free
            if not wave or not target:
                return False
            visited = wave
            while wave and not wave & target:
                wave = (((wave & fine_not_right) << 1) |
                        ((wave & fine_not_left) >> 1) |
                        (wave << fw) | (wave >> fw)) & free & ~visited
                visited |= wave
            if not wave:
                return False
        return True

    def output(chosen):
        return {'scene_id': spec['scene_id'],
                'objects': fixed + [poses[i][a] for i, a in enumerate(chosen)]}

    def first_completion(ds, budget):
        budget[0] -= 1
        if budget[0] < 0:
            return None
        remaining = [i for i in range(n) if ds[i] & (ds[i] - 1)]
        if not remaining:
            return [d.bit_length() - 1 for d in ds]
        i = min(remaining, key=lambda k: (ds[k].bit_count(), -len(neighbors[k])))
        rest = ds[i]
        while rest:
            bit = rest & -rest
            rest -= bit
            child = list(ds)
            child[i] = bit
            if propagate(child, [i]):
                found = first_completion(child, budget)
                if found is not None:
                    return found
            if budget[0] < 0:
                break
        return None

    if any(not d for d in domains) or not propagate(domains, range(n)):
        return {'scene_id': spec['scene_id'], 'objects': fixed +
                [packet['domains'][name][0] for name in names]}
    baseline = first_completion(list(domains), [2500])
    best = [baseline, -1, False]
    if baseline is not None:
        blocked = fixed_mask
        for i, a in enumerate(baseline):
            blocked |= blocks[i][a]
        distance = navigation(blocked)[0]
        if distance >= 0 and accessible(baseline):
            best[:] = [baseline, distance, True]
            if distance >= minimum:
                return output(baseline)

    dead = set()
    visits = [0]

    def search(ds):
        visits[0] += 1
        if visits[0] > 65000:
            return None
        original_key = tuple(ds)
        if original_key in dead:
            return None
        owners = []
        hit_domains = {}
        while True:
            certain, possible = fixed_mask, fixed_mask
            remaining = []
            for i, d in enumerate(ds):
                a, b = envelope(i, d)
                certain |= a
                possible |= b
                if d & (d - 1):
                    remaining.append(i)
            lower, short_path = navigation(certain)
            if lower < 0:
                return None
            if not remaining:
                chosen = [d.bit_length() - 1 for d in ds]
                if lower >= minimum:
                    if accessible(chosen):
                        return chosen
                return None
            if possible == certain:
                if lower < minimum:
                    return None
            else:
                upper = navigation(possible)[0]
                if 0 <= upper < minimum:
                    return None
            owners, hit_domains = [], {}
            if short_path:
                for i in remaining:
                    rest, hit = ds[i], 0
                    while rest:
                        bit = rest & -rest
                        rest -= bit
                        if blocks[i][bit.bit_length() - 1] & short_path:
                            hit |= bit
                    if hit:
                        owners.append(i)
                        hit_domains[i] = hit
                if not owners:
                    return None
                if len(owners) == 1:
                    i = owners[0]
                    if hit_domains[i] != ds[i]:
                        ds[i] = hit_domains[i]
                        if not propagate(ds, [i]):
                            return None
                        continue
            break
        pool = owners if owners else remaining
        i = min(pool, key=lambda k: (
            ds[k].bit_count(),
            -sum(1 for j in neighbors[k] if ds[j] & (ds[j] - 1)),
            -len(neighbors[k])))
        candidates = []
        rest = ds[i]
        while rest:
            bit = rest & -rest
            rest -= bit
            a = bit.bit_length() - 1
            distance = navigation(certain | blocks[i][a])[0]
            if distance < 0:
                continue
            support = sum((compatibility[i][j][a] & ds[j]).bit_count()
                          for j in neighbors[i])
            hit = bool(bit & hit_domains.get(i, 0))
            score = (distance >= minimum, distance if distance < minimum else -distance,
                     hit, support)
            candidates.append((score, bit))
        candidates.sort(reverse=True)
        for score, bit in candidates:
            child = list(ds)
            child[i] = bit
            if propagate(child, [i]):
                found = search(child)
                if found is not None:
                    return found
            if visits[0] > 65000:
                return None
        if len(dead) < 16000:
            dead.add(original_key)
        return None

    result = search(list(domains))
    if result is not None:
        return output(result)
    if best[0] is not None:
        return output(best[0])
    fallback = first_completion(list(domains), [12000])
    if fallback is not None:
        return output(fallback)
    return {'scene_id': spec['scene_id'], 'objects': fixed +
            [packet['domains'][name][0] for name in names]}
