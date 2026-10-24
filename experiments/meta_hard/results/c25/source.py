import math
from collections import deque

def layout(packet):
    spec = packet['spec']
    width, depth = packet['width'], packet['depth']
    assets = spec['assets']
    fixed = list(packet['fixed'])
    fixed_ids = {o['id'] for o in fixed}
    required = [o for o in spec['required_objects'] if o['id'] not in fixed_ids]
    regions = {r['id']: r for r in spec.get('regions', [])}
    constraints = spec.get('constraints', [])
    accesses = spec.get('access_paths', [])
    minimum = packet['minimum_steps']
    eps = 1e-8
    budget = [0, 0, 0]

    def exhausted():
        return budget[0] > 2800000 or budget[1] > 480000 or budget[2] > 45000

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

    def front(subject, sr, reference, rr, limit):
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[int(reference['yaw_deg'] % 360 // 90)]
        dx, dy = subject['x'] - reference['x'], subject['y'] - reference['y']
        hx = (sr[2] - sr[0] + rr[2] - rr[0]) / 2
        hy = (sr[3] - sr[1] + rr[3] - rr[1]) / 2
        gap = dx * fx + dy * fy - abs(fx) * hx - abs(fy) * hy
        return (-eps <= gap <= limit + eps and
                abs(dx * fy - dy * fx) <= abs(fy) * hx + abs(fx) * hy + eps)

    clear_regions = []
    walls = {}
    self_front = set()
    for c in constraints:
        if c['type'] == 'keep_clear':
            r = regions[c['region']]
            clear_regions.append((r['x'], r['y'], r['x'] + r['width'], r['y'] + r['depth']))
        elif c['type'] == 'near_wall':
            walls.setdefault(c['subject'], []).append((c['wall'], c['max_gap']))
        elif c['type'] == 'in_front_of' and c['subject'] == c['reference']:
            self_front.add(c['subject'])

    def unary(o, r):
        if r[0] < -eps or r[1] < -eps or r[2] > width + eps or r[3] > depth + eps:
            return False
        if o['id'] in self_front or any(overlaps(r, z) for z in clear_regions):
            return False
        for wall, limit in walls.get(o['id'], []):
            gap = {'west': r[0], 'east': width - r[2],
                   'south': r[1], 'north': depth - r[3]}[wall]
            if not -eps <= gap <= limit + eps:
                return False
        return True

    def grid(w, h):
        full = (1 << (w * h)) - 1
        left = sum(1 << (y * w) for y in range(h))
        right = left << (w - 1)
        return (w, h, full, full ^ left, full ^ right)

    nav = grid(int(width), int(depth))
    start = int(packet['start'][1]) * nav[0] + int(packet['start'][0])
    goal = int(packet['goal'][1]) * nav[0] + int(packet['goal'][0])
    start_bit, goal_bit = 1 << start, 1 << goal
    endpoints = start_bit | goal_bit

    def blocked_mask(r, g, scale, clearance):
        w, h = g[0], g[1]
        result = 0
        xlo = max(0, int(math.floor((r[0] - clearance) / scale - 0.5)))
        xhi = min(w - 1, int(math.ceil((r[2] + clearance) / scale - 0.5)))
        ylo = max(0, int(math.floor((r[1] - clearance) / scale - 0.5)))
        yhi = min(h - 1, int(math.ceil((r[3] + clearance) / scale - 0.5)))
        row = 0
        for x in range(xlo, xhi + 1):
            if r[0] - clearance < (x + 0.5) * scale < r[2] + clearance:
                row |= 1 << x
        for y in range(ylo, yhi + 1):
            if r[1] - clearance < (y + 0.5) * scale < r[3] + clearance:
                result |= row << (y * w)
        return result

    def neighbors(bits, g):
        return (((bits & g[4]) << 1) | ((bits & g[3]) >> 1) |
                (bits << g[0]) | (bits >> g[0])) & g[2]

    walk_cache = {}

    def walk(blocked, want_path=False):
        if blocked & endpoints:
            return -1, 0
        cached = walk_cache.get(blocked)
        if cached is not None and (not want_path or cached[1] or cached[0] < 0):
            return cached
        free = nav[2] & ~blocked
        wave = start_bit
        seen = wave
        layers = []
        distance = 0
        result = (-1, 0)
        while wave:
            budget[1] += 1
            if want_path:
                layers.append(wave)
            if wave & goal_bit:
                path = 0
                if want_path:
                    cell = goal_bit
                    path = cell
                    for layer in reversed(layers[:-1]):
                        choices = neighbors(cell, nav) & layer
                        cell = choices & -choices
                        path |= cell
                result = (distance, path)
                break
            wave = neighbors(wave, nav) & free & ~seen
            seen |= wave
            distance += 1
        if len(walk_cache) >= 8192:
            walk_cache.clear()
        walk_cache[blocked] = result
        return result

    names = [o['id'] for o in fixed] + [o['id'] for o in required]
    index = {name: i for i, name in enumerate(names)}
    poses, rects, masks = [], [], []
    raw_domains = [[o] for o in fixed]
    for req in required:
        raw_domains.append([o for o in packet['domains'].get(req['id'], [])
                            if o['id'] == req['id'] and o['asset'] == req['asset']])
    for raw in raw_domains:
        records = []
        duplicates = set()
        for o in raw:
            key = (o['asset'], o['x'], o['y'], o['yaw_deg'])
            if key in duplicates:
                continue
            duplicates.add(key)
            r = rectangle(o)
            m = blocked_mask(r, nav, 1.0, 0.15)
            if unary(o, r) and not m & endpoints:
                records.append((o, r, m))
        records.sort(key=lambda v: (v[0]['x'], v[0]['y'], v[0]['yaw_deg']))
        poses.append([v[0] for v in records])
        rects.append([v[1] for v in records])
        masks.append([v[2] for v in records])
    n = len(names)
    initial = [(1 << len(p)) - 1 for p in poses]
    relations = {}
    for c in constraints:
        if c['type'] == 'in_front_of' and c['subject'] != c['reference']:
            i, j = index[c['subject']], index[c['reference']]
            relations.setdefault((min(i, j), max(i, j)), []).append((i, c['max_gap']))
    support = [[None for j in range(n)] for i in range(n)]
    links = [[] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            si = [0] * len(poses[i])
            sj = [0] * len(poses[j])
            rels = relations.get((i, j), [])
            for a, oa in enumerate(poses[i]):
                ra = rects[i][a]
                for b, ob in enumerate(poses[j]):
                    rb = rects[j][b]
                    if overlaps(ra, rb):
                        continue
                    good = True
                    for subject, limit in rels:
                        if subject == i:
                            good = front(oa, ra, ob, rb, limit)
                        else:
                            good = front(ob, rb, oa, ra, limit)
                        if not good:
                            break
                    if good:
                        si[a] |= 1 << b
                        sj[b] |= 1 << a
            support[i][j], support[j][i] = si, sj
            if any(s != initial[j] for s in si) or any(s != initial[i] for s in sj):
                links[i].append(j)
                links[j].append(i)

    def propagate(domains, changed):
        queue = deque()
        pending = set()
        for j in changed:
            for i in links[j]:
                key = i * n + j
                if key not in pending:
                    pending.add(key)
                    queue.append((i, j))
        while queue:
            i, j = queue.popleft()
            pending.remove(i * n + j)
            old = domains[i]
            values = old
            new = 0
            sup, other = support[i][j], domains[j]
            while values:
                budget[0] += 1
                bit = values & -values
                values -= bit
                if sup[bit.bit_length() - 1] & other:
                    new |= bit
            if new != old:
                if not new:
                    return False
                domains[i] = new
                for k in links[i]:
                    if k != j:
                        key = k * n + i
                        if key not in pending:
                            pending.add(key)
                            queue.append((k, i))
            if budget[0] > 2800000:
                return False
        return True

    fine = grid(int(math.ceil(width * 4)), int(math.ceil(depth * 4)))
    fine_valid = 0
    points = []
    if accesses:
        for y in range(fine[1]):
            for x in range(fine[0]):
                px, py = (x + 0.5) / 4, (y + 0.5) / 4
                bit = 1 << (y * fine[0] + x)
                if px < width and py < depth:
                    fine_valid |= bit
                    points.append((px, py, bit))
    access_cache = {}
    target_cache = {}
    source_masks = {}
    for access in accesses:
        rid = access['from_region']
        if rid not in source_masks:
            r = regions[rid]
            source_masks[rid] = sum(bit for x, y, bit in points
                if r['x'] <= x <= r['x'] + r['width'] and
                   r['y'] <= y <= r['y'] + r['depth'])

    def accessible(choice):
        reached_cache = {}
        for access in accesses:
            clearance = access.get('clearance', 0.15)
            rid = access['from_region']
            reach_key = (rid, clearance)
            if reach_key in reached_cache:
                reached = reached_cache[reach_key]
            else:
                blocked = 0
                for i, a in enumerate(choice):
                    key = (i, a, clearance)
                    if key not in access_cache:
                        access_cache[key] = blocked_mask(rects[i][a], fine, 0.25, clearance)
                    blocked |= access_cache[key]
                free = fine_valid & ~blocked
                reached = source_masks[rid] & free
                if not reached:
                    return False
                wave = reached
                while wave:
                    wave = neighbors(wave, fine) & free & ~reached
                    reached |= wave
                reached_cache[reach_key] = reached
            i = index[access['to_object']]
            a = choice[i]
            limit = access.get('interaction_distance', 0.8)
            key = (i, a, limit)
            if key not in target_cache:
                r = rects[i][a]
                target = 0
                for x, y, bit in points:
                    dx = max(r[0] - x, 0, x - r[2])
                    dy = max(r[1] - y, 0, y - r[3])
                    if dx * dx + dy * dy <= limit * limit + eps:
                        target |= bit
                target_cache[key] = target
            if not reached & target_cache[key]:
                return False
        return True

    intersections = [{} for i in range(n)]

    def mandatory_mask(domains):
        mandatory = 0
        for i, d in enumerate(domains):
            cache = intersections[i]
            intersection = cache.get(d)
            if intersection is None:
                intersection = nav[2]
                values = d
                while values and intersection:
                    bit = values & -values
                    values -= bit
                    intersection &= masks[i][bit.bit_length() - 1]
                if len(cache) >= 1024:
                    cache.clear()
                cache[d] = intersection
            mandatory |= intersection
        return mandatory

    best = [None, -1]
    failed = set()

    def search(domains, target):
        budget[2] += 1
        if exhausted():
            return None
        state = tuple(domains)
        if state in failed:
            return None
        mandatory = mandatory_mask(domains)
        options = [i for i, d in enumerate(domains) if d & (d - 1)]
        distance, path = walk(mandatory, bool(options) and target > 0)
        if distance < 0:
            return None
        if not options:
            choice = [d.bit_length() - 1 for d in domains]
            if distance <= best[1] and distance < target:
                return None
            if accessible(choice):
                if distance > best[1]:
                    best[:] = [choice, distance]
                if distance >= target:
                    return choice
            return None
        hits = {}
        full_hits = []
        if distance < target:
            for i in options:
                values = domains[i]
                hit = 0
                while values:
                    bit = values & -values
                    values -= bit
                    if masks[i][bit.bit_length() - 1] & path:
                        hit |= bit
                if hit:
                    hits[i] = hit
                    if hit == domains[i]:
                        full_hits.append(i)
            if not hits:
                if len(failed) < 16000:
                    failed.add(state)
                return None
            if len(hits) == 1:
                i = next(iter(hits))
                if hits[i] != domains[i]:
                    new = domains[:]
                    new[i] = hits[i]
                    if propagate(new, [i]):
                        return search(new, target)
                    return None
            elif not full_hits:
                # Every completion must block this particular short path.
                # Split a domain into path-hitting and path-avoiding poses,
                # retaining groups of equivalent choices until necessary.
                i = min(hits, key=lambda j: (
                    hits[j].bit_count(),
                    -len(links[j]),
                    domains[j].bit_count()))
                for subset in (hits[i], domains[i] & ~hits[i]):
                    new = domains[:]
                    new[i] = subset
                    if propagate(new, [i]):
                        result = search(new, target)
                        if result is not None:
                            return result
                    if exhausted():
                        return None
                if len(failed) < 16000:
                    failed.add(state)
                return None
        candidates = full_hits if full_hits else options
        i = min(candidates, key=lambda j: (
            domains[j].bit_count(),
            0 if j in hits else 1,
            -sum(1 for k in links[j] if domains[k] & (domains[k] - 1))))
        ranked = []
        values = domains[i]
        for_unused = 0
        while values:
            bit = values & -values
            values -= bit
            a = bit.bit_length() - 1
            d, unused = walk(mandatory | masks[i][a])
            if d >= 0:
                flexibility = sum((support[i][j][a] & domains[j]).bit_count()
                                  for j in links[i] if domains[j] & (domains[j] - 1))
                ranked.append((d, (masks[i][a] & path).bit_count(), flexibility, bit))
            if exhausted():
                return None
        ranked.sort(reverse=True)
        for distance_score, path_score, flexibility, bit in ranked:
            new = domains[:]
            new[i] = bit
            if propagate(new, [i]):
                result = search(new, target)
                if result is not None:
                    return result
            if exhausted():
                return None
        if len(failed) < 16000:
            failed.add(state)
        return None

    choice = None
    if all(initial) and propagate(initial, range(n)):
        # Establish a checked fallback before spending the remaining budget
        # on the hard shortest-distance requirement.
        choice = search(initial[:], 0)
        failed.clear()
        if best[1] < minimum and not exhausted():
            improved = search(initial[:], minimum)
            if improved is not None:
                choice = improved
        if best[0] is not None:
            choice = best[0]
    if choice is not None:
        objects = fixed + [poses[i][choice[i]] for i in range(len(fixed), n)]
    else:
        objects = fixed[:]
        for k, req in enumerate(required):
            i = len(fixed) + k
            domain = poses[i] or packet['domains'].get(req['id'], [])
            if domain:
                objects.append(domain[0])
    return {'scene_id': spec['scene_id'], 'objects': objects}
