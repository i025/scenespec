import math

def layout(packet):
    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    fixed = list(packet['fixed'])
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    regions = {r['id']: r for r in spec.get('regions', [])}
    relations = []
    walls = {}
    clear = []
    for c in spec.get('constraints', []):
        if c['type'] == 'in_front_of':
            relations.append(c)
        elif c['type'] == 'near_wall':
            walls.setdefault(c['subject'], []).append(c)
        elif c['type'] == 'keep_clear':
            r = regions[c['region']]
            clear.append((r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth']))

    def rectangle(o):
        a = assets[o['asset']]
        w, h = a['width'], a['depth']
        if o['yaw_deg'] % 180:
            w, h = h, w
        return (o['x'] - w / 2, o['y'] - h / 2, o['x'] + w / 2, o['y'] + h / 2)

    def overlap(a, b):
        return min(a[2], b[2]) - max(a[0], b[0]) > 1e-8 and min(a[3], b[3]) - max(a[1], b[1]) > 1e-8

    def front(c, s, t, a, b):
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[int(t['yaw_deg'] % 360) // 90]
        dx, dy = s['x'] - t['x'], s['y'] - t['y']
        sx = (a[2] - a[0] + b[2] - b[0]) / 2
        sy = (a[3] - a[1] + b[3] - b[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * sx - abs(fy) * sy
        return -1e-8 <= gap <= c['max_gap'] + 1e-8 and abs(dx * fy - dy * fx) <= abs(fy) * sx + abs(fx) * sy + 1e-8

    def grid(w, h):
        full = (1 << (w * h)) - 1
        left = sum(1 << (y * w) for y in range(h))
        return (w, h, full, full ^ left, full ^ (left << (w - 1)))

    def expand(bits, g):
        w, h, full, noleft, noright = g
        return (((bits & noright) << 1) | ((bits & noleft) >> 1) | (bits << w) | (bits >> w)) & full

    def rect_mask(r, clearance, scale, w, h):
        lx, ly = r[0] - clearance, r[1] - clearance
        hx, hy = r[2] + clearance, r[3] + clearance
        x0 = max(0, int(math.floor(lx * scale - .5)))
        x1 = min(w, int(math.ceil(hx * scale - .5)) + 1)
        y0 = max(0, int(math.floor(ly * scale - .5)))
        y1 = min(h, int(math.ceil(hy * scale - .5)) + 1)
        while x0 < x1 and not lx < (x0 + .5) / scale < hx:
            x0 += 1
        while x1 > x0 and not lx < (x1 - .5) / scale < hx:
            x1 -= 1
        if x0 >= x1:
            return 0
        row = ((1 << (x1 - x0)) - 1) << x0
        result = 0
        for y in range(y0, y1):
            if ly < (y + .5) / scale < hy:
                result |= row << (y * w)
        return result

    w, h = int(width), int(depth)
    nav = grid(w, h)
    start = 1 << (int(packet['start'][1]) * w + int(packet['start'][0]))
    goal = 1 << (int(packet['goal'][1]) * w + int(packet['goal'][0]))
    endpoints = start | goal
    minimum = packet['minimum_steps']
    fixed_rects = [rectangle(o) for o in fixed]
    fixed_by_id = {o['id']: (o, r) for o, r in zip(fixed, fixed_rects)}
    fixed_mask = 0
    for r in fixed_rects:
        fixed_mask |= rect_mask(r, .15, 1, w, h)
    names = [o['id'] for o in spec['required_objects'] if o['id'] not in fixed_by_id]
    references = {c['reference'] for c in relations}
    poses, rects, masks = [], [], []
    for name in names:
        pp, rr, mm, seen = [], [], [], set()
        unary = [c for c in relations if (c['subject'] == name and c['reference'] in fixed_by_id) or (c['reference'] == name and c['subject'] in fixed_by_id)]
        candidates = sorted(packet['domains'].get(name, []), key=lambda o: (o['x'], o['y'], o['yaw_deg']))
        for o in candidates:
            if o['id'] != name or o['asset'] != required[name]:
                continue
            r = rectangle(o)
            if r[0] < -1e-8 or r[1] < -1e-8 or r[2] > width + 1e-8 or r[3] > depth + 1e-8:
                continue
            if any(overlap(r, z) for z in clear) or any(overlap(r, z) for z in fixed_rects):
                continue
            good = True
            for c in walls.get(name, []):
                gap = {'west': r[0], 'south': r[1], 'east': width - r[2], 'north': depth - r[3]}[c['wall']]
                if not -1e-8 <= gap <= c['max_gap'] + 1e-8:
                    good = False
                    break
            if not good:
                continue
            for c in unary:
                if c['subject'] == name:
                    other, other_r = fixed_by_id[c['reference']]
                    good = front(c, o, other, r, other_r)
                else:
                    other, other_r = fixed_by_id[c['subject']]
                    good = front(c, other, o, other_r, r)
                if not good:
                    break
            if not good:
                continue
            m = rect_mask(r, .15, 1, w, h)
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
    n = len(names)
    domains = [(1 << len(p)) - 1 for p in poses]
    compat = [[None for j in range(n)] for i in range(n)]
    neighbors = [[] for i in range(n)]
    degrees = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            rel = [c for c in relations if (c['subject'] == names[i] and c['reference'] == names[j]) or (c['subject'] == names[j] and c['reference'] == names[i])]
            if rel:
                degrees[i] += 1
                degrees[j] += 1
            ij = [0] * len(poses[i])
            ji = [0] * len(poses[j])
            unrestricted = True
            for a, ra in enumerate(rects[i]):
                support = 0
                for b, rb in enumerate(rects[j]):
                    if overlap(ra, rb):
                        unrestricted = False
                        continue
                    good = True
                    for c in rel:
                        if c['subject'] == names[i]:
                            good = front(c, poses[i][a], poses[j][b], ra, rb)
                        else:
                            good = front(c, poses[j][b], poses[i][a], rb, ra)
                        if not good:
                            break
                    if good:
                        support |= 1 << b
                        ji[b] |= 1 << a
                    else:
                        unrestricted = False
                ij[a] = support
            if not unrestricted:
                compat[i][j], compat[j][i] = ij, ji
                neighbors[i].append(j)
                neighbors[j].append(i)

    work = [0, 0]
    limits = (650000, 90000)

    def exhausted():
        return work[0] >= limits[0] or work[1] >= limits[1]

    def propagate(ds, changed):
        queue = list(changed)
        pending = set(queue)
        while queue:
            j = queue.pop()
            pending.discard(j)
            dj = ds[j]
            if not dj:
                return False
            singleton = not dj & (dj - 1)
            a = dj.bit_length() - 1
            for i in neighbors[j]:
                old = ds[i]
                if singleton:
                    work[0] += 1
                    new = old & compat[j][i][a]
                else:
                    new = 0
                    options = old
                    table = compat[i][j]
                    work[0] += old.bit_count()
                    while options:
                        bit = options & -options
                        options -= bit
                        if table[bit.bit_length() - 1] & dj:
                            new |= bit
                if not new or exhausted():
                    return False
                if old != new:
                    ds[i] = new
                    if i not in pending:
                        queue.append(i)
                        pending.add(i)
        return True

    nav_cache = {}

    def walk(blocked):
        cached = nav_cache.get(blocked)
        if cached is not None:
            return cached
        if blocked & endpoints:
            return (-1, 0)
        free = nav[2] & ~blocked
        wave = start
        reached = start
        layers = [start]
        while not wave & goal:
            work[1] += 1
            wave = expand(wave, nav) & free & ~reached
            if not wave:
                result = (-1, 0)
                break
            reached |= wave
            layers.append(wave)
        else:
            at, path = goal, goal
            for k in range(len(layers) - 2, -1, -1):
                choices = expand(at, nav) & layers[k]
                at = choices & -choices
                path |= at
            result = (len(layers) - 1, path)
        if len(nav_cache) < 10000:
            nav_cache[blocked] = result
        return result

    access_data = []
    access_ready = [False]

    def prepare_access():
        scale = 10
        fw, fh = int(width * scale), int(depth * scale)
        fine = grid(fw, fh)
        clearance_cache = {}
        for access in spec.get('access_paths', []):
            clearance = access.get('clearance', .15)
            shared = clearance_cache.get(clearance)
            if shared is None:
                base = 0
                for r in fixed_rects:
                    base |= rect_mask(r, clearance, scale, fw, fh)
                edge_row = 0
                for x in range(fw):
                    xx = (x + .5) / scale
                    if xx < clearance or xx > width - clearance:
                        edge_row |= 1 << x
                full_row = (1 << fw) - 1
                for y in range(fh):
                    yy = (y + .5) / scale
                    row = full_row if yy < clearance or yy > depth - clearance else edge_row
                    base |= row << (y * fw)
                blocks = [[rect_mask(r, clearance, scale, fw, fh) for r in rr] for rr in rects]
                shared = (base, blocks)
                clearance_cache[clearance] = shared
            base, blocks = shared
            region = regions[access['from_region']]
            seeds = 0
            row = 0
            for x in range(fw):
                if region['x'] <= (x + .5) / scale <= region['x'] + region['width']:
                    row |= 1 << x
            for y in range(fh):
                if region['y'] <= (y + .5) / scale <= region['y'] + region['depth']:
                    seeds |= row << (y * fw)
            target = access['to_object']
            ti = names.index(target) if target not in fixed_by_id else -1
            tr = rects[ti] if ti >= 0 else [fixed_by_id[target][1]]
            distance = max(0, access.get('interaction_distance', .8) - .015)
            targets = []
            for r in tr:
                bits = 0
                x0 = max(0, int(math.floor((r[0] - distance) * scale - .5)))
                x1 = min(fw, int(math.ceil((r[2] + distance) * scale - .5)) + 1)
                y0 = max(0, int(math.floor((r[1] - distance) * scale - .5)))
                y1 = min(fh, int(math.ceil((r[3] + distance) * scale - .5)) + 1)
                for y in range(y0, y1):
                    yy = (y + .5) / scale
                    dy = max(r[1] - yy, 0, yy - r[3])
                    row = 0
                    for x in range(x0, x1):
                        xx = (x + .5) / scale
                        dx = max(r[0] - xx, 0, xx - r[2])
                        if dx * dx + dy * dy <= distance * distance:
                            row |= 1 << x
                    bits |= row << (y * fw)
                targets.append(bits)
            access_data.append((fine, base, blocks, seeds, targets, ti, {}))
        access_ready[0] = True

    def accessible(selection):
        if not access_ready[0]:
            prepare_access()
        for fine, base, blocks, seeds, targets, ti, cache in access_data:
            blocked = base
            for i, a in enumerate(selection):
                blocked |= blocks[i][a]
            target = targets[selection[ti] if ti >= 0 else 0]
            key = (blocked, target)
            if key in cache:
                if not cache[key]:
                    return False
                continue
            free = fine[2] & ~blocked
            wave = seeds & free
            reached = wave
            target &= free
            while wave and not wave & target:
                if exhausted():
                    return False
                work[1] += 1
                wave = expand(wave, fine) & free & ~reached
                reached |= wave
            good = bool(wave & target)
            if len(cache) < 1200:
                cache[key] = good
            if not good:
                return False
        return True

    state = [0, 300, 0, None, -1]
    failed = set()
    cuts = []
    cut_paths = set()

    def path_hits(path):
        result = []
        for i in range(n):
            hit = 0
            for a, m in enumerate(masks[i]):
                if m & path:
                    hit |= 1 << a
            if hit:
                result.append((i, hit))
        return result

    def enforce_cuts(ds):
        while True:
            changed = set()
            for cut in cuts:
                sole = -1
                active = 0
                satisfied = False
                for i, hit in cut:
                    d = ds[i]
                    possible = d & hit
                    if possible:
                        if possible == d:
                            satisfied = True
                            break
                        if sole >= 0:
                            sole = -2
                            break
                        sole, active = i, possible
                if satisfied or sole == -2:
                    continue
                if sole < 0:
                    return False
                if ds[sole] != active:
                    ds[sole] = active
                    changed.add(sole)
            if not changed:
                return True
            if not propagate(ds, changed):
                return False

    def search(ds):
        state[0] += 1
        if state[0] > state[1] or exhausted():
            return False
        if cuts and not enforce_cuts(ds):
            return False
        key = tuple(ds)
        if key in failed:
            return False
        while True:
            blocked = fixed_mask
            undecided = []
            for i, d in enumerate(ds):
                if not d:
                    return False
                if d & (d - 1):
                    undecided.append(i)
                forced = nav[2]
                options = d
                while options:
                    bit = options & -options
                    options -= bit
                    forced &= masks[i][bit.bit_length() - 1]
                    if not forced:
                        break
                blocked |= forced
            distance, path = walk(blocked)
            if distance < 0:
                failed.add(key)
                return False
            if not undecided:
                selection = [d.bit_length() - 1 for d in ds]
                if (distance > state[4] or distance >= state[2]) and accessible(selection):
                    if distance > state[4]:
                        state[3], state[4] = selection, distance
                    if distance >= state[2]:
                        return True
                failed.add(key)
                return False
            hits = []
            if distance < state[2]:
                all_hits = path_hits(path)
                if path not in cut_paths and len(cuts) < 64:
                    cut_paths.add(path)
                    cuts.append(all_hits)
                for i, hit in all_hits:
                    if ds[i] & (ds[i] - 1):
                        active = hit & ds[i]
                        if active:
                            hits.append((i, active))
                if not hits:
                    failed.add(key)
                    return False
                if len(hits) == 1 and hits[0][1] != ds[hits[0][0]]:
                    i, hit = hits[0]
                    ds[i] = hit
                    if not propagate(ds, [i]) or not enforce_cuts(ds):
                        failed.add(key)
                        return False
                    continue
            break

        universal = [i for i, hit in hits if hit == ds[i]]
        if hits and not universal:
            hits.sort(key=lambda ih: (ih[1].bit_count(), -degrees[ih[0]], -(ds[ih[0]].bit_count() - ih[1].bit_count())))
            prefix = ds[:]
            for i, hit in hits:
                active = prefix[i] & hit
                if active:
                    child = prefix[:]
                    child[i] = active
                    if propagate(child, [i]) and search(child):
                        return True
                if exhausted() or state[0] > state[1]:
                    return False
                remaining = prefix[i] & ~hit
                if not remaining:
                    break
                prefix[i] = remaining
                if not propagate(prefix, [i]):
                    break
            failed.add(key)
            return False

        choices = universal if universal else undecided
        i = min(choices, key=lambda j: (ds[j].bit_count(), -degrees[j], -len(neighbors[j])))
        ranked = []
        options = ds[i]
        while options:
            bit = options & -options
            options -= bit
            a = bit.bit_length() - 1
            d, unused_path = walk(blocked | masks[i][a])
            if d < 0:
                continue
            support = 0
            for j in undecided:
                if j != i:
                    table = compat[i][j]
                    support += ds[j].bit_count() if table is None else (table[a] & ds[j]).bit_count()
            rank = -d if distance < state[2] else d
            ranked.append((rank, -support, a))
        ranked.sort()
        for rank, support, a in ranked:
            if exhausted() or state[0] > state[1]:
                return False
            child = ds[:]
            child[i] = 1 << a
            if propagate(child, [i]) and search(child):
                return True
        failed.add(key)
        return False

    if not fixed_mask & endpoints and all(domains) and propagate(domains, range(n)):
        initial = domains[:]
        search(initial[:])
        if state[4] < minimum and not exhausted():
            state[0], state[1], state[2] = 0, 16000, minimum
            failed.clear()
            search(initial[:])
    selection = state[3]
    objects = list(fixed)
    if selection is not None:
        objects.extend(poses[i][a] for i, a in enumerate(selection))
    else:
        for i, name in enumerate(names):
            if poses[i]:
                objects.append(poses[i][0])
            else:
                objects.append(packet['domains'][name][0])
    return {'scene_id': spec['scene_id'], 'objects': objects}
