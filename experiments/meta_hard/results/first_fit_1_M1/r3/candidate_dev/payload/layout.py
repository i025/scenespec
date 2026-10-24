import math
from collections import deque


def layout(packet):
    spec = packet['spec']
    assets = spec['assets']
    width, depth = packet['width'], packet['depth']
    fixed = list(packet['fixed'])
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    fixed_map = {o['id']: o for o in fixed}
    constraints = spec.get('constraints', [])
    regions = {r['id']: r for r in spec.get('regions', [])}
    eps = 1e-8

    def rectangle(o):
        a = assets[o['asset']]
        w, d = a['width'], a['depth']
        if o['yaw_deg'] % 180:
            w, d = d, w
        return (o['x']-w/2, o['y']-d/2, o['x']+w/2, o['y']+d/2)

    def overlaps(a, b):
        return min(a[2], b[2])-max(a[0], b[0]) > eps and min(a[3], b[3])-max(a[1], b[1]) > eps

    def front(subject, reference, c):
        a, b = rectangle(subject), rectangle(reference)
        fx, fy = ((0, 1), (-1, 0), (0, -1), (1, 0))[(reference['yaw_deg'] % 360)//90]
        dx, dy = subject['x']-reference['x'], subject['y']-reference['y']
        sx = (a[2]-a[0]+b[2]-b[0])/2
        sy = (a[3]-a[1]+b[3]-b[1])/2
        gap = dx*fx+dy*fy-abs(fx)*sx-abs(fy)*sy
        return -eps <= gap <= c['max_gap']+eps and abs(dx*fy-dy*fx) <= abs(fy)*sx+abs(fx)*sy+eps

    keep, walls, relations, references = [], {}, [], set()
    for c in constraints:
        if c['type'] == 'keep_clear':
            r = regions[c['region']]
            keep.append((r['x'], r['y'], r['x']+r['width'], r['y']+r['depth']))
        elif c['type'] == 'near_wall':
            walls.setdefault(c['subject'], []).append(c)
        elif c['type'] == 'in_front_of':
            relations.append(c)
            references.add(c['reference'])
    fixed_rects = [rectangle(o) for o in fixed]

    def unary(o):
        r = rectangle(o)
        if r[0] < -eps or r[1] < -eps or r[2] > width+eps or r[3] > depth+eps:
            return False
        if any(overlaps(r, z) for z in keep) or any(overlaps(r, z) for z in fixed_rects):
            return False
        for c in walls.get(o['id'], []):
            gap = {'north': depth-r[3], 'south': r[1], 'east': width-r[2], 'west': r[0]}[c['wall']]
            if not -eps <= gap <= c['max_gap']+eps:
                return False
        for c in relations:
            if c['subject'] == o['id'] and c['reference'] in fixed_map:
                if not front(o, fixed_map[c['reference']], c):
                    return False
            if c['reference'] == o['id'] and c['subject'] in fixed_map:
                if not front(fixed_map[c['subject']], o, c):
                    return False
        return True

    def blocked_cells(r, clearance, nx, ny, step):
        mask = 0
        x0 = max(0, int(math.floor((r[0]-clearance)/step-0.5))+1)
        x1 = min(nx-1, int(math.ceil((r[2]+clearance)/step-0.5))-1)
        y0 = max(0, int(math.floor((r[1]-clearance)/step-0.5))+1)
        y1 = min(ny-1, int(math.ceil((r[3]+clearance)/step-0.5))-1)
        if x1 >= x0 and y1 >= y0:
            row = ((1 << (x1-x0+1))-1) << x0
            for y in range(y0, y1+1):
                mask |= row << (y*nx)
        return mask

    gw, gh = int(width), int(depth)
    full = (1 << (gw*gh))-1
    left = sum(1 << (y*gw) for y in range(gh))
    right = left << (gw-1)
    sb = 1 << (int(packet['start'][0])+gw*int(packet['start'][1]))
    gb = 1 << (int(packet['goal'][0])+gw*int(packet['goal'][1]))
    endpoints = sb | gb
    minimum = packet['minimum_steps']

    def adjacent(bits):
        return (((bits << 1) & ~left) | ((bits >> 1) & ~right) | (bits << gw) | (bits >> gw)) & full

    nav_cache = {}

    def navigation(blocked):
        cached = nav_cache.get(blocked)
        if cached is not None:
            return cached
        if blocked & endpoints:
            return -1, 0
        available = full & ~blocked
        frontier, seen = sb, sb
        levels = [sb]
        while frontier and not frontier & gb:
            frontier = adjacent(frontier) & available & ~seen
            seen |= frontier
            levels.append(frontier)
        if not frontier:
            result = (-1, 0)
        else:
            position, path = gb, gb
            for level in reversed(levels[:-1]):
                choices = adjacent(position) & level
                position = choices & -choices
                path |= position
            result = (len(levels)-1, path)
        if len(nav_cache) >= 2048:
            nav_cache.clear()
        nav_cache[blocked] = result
        return result

    fixed_blocked = 0
    for r in fixed_rects:
        fixed_blocked |= blocked_cells(r, 0.15, gw, gh, 1.0)
    names = [o['id'] for o in spec['required_objects'] if o['id'] not in fixed_map]
    poses, rects, masks = [], [], []
    for name in names:
        ps, rs, ms, seen = [], [], [], set()
        for o in packet['domains'].get(name, []):
            if o['id'] != name or o['asset'] != required[name] or not unary(o):
                continue
            r = rectangle(o)
            key = r+(o['yaw_deg'] % 360 if name in references else 0,)
            if key in seen:
                continue
            seen.add(key)
            b = blocked_cells(r, 0.15, gw, gh, 1.0)
            if b & endpoints:
                continue
            ps.append(o)
            rs.append(r)
            ms.append(b)
        poses.append(ps)
        rects.append(rs)
        masks.append(ms)
    n = len(names)
    index = {name: i for i, name in enumerate(names)}
    pair_relations = {}
    degree = [0]*n
    for c in relations:
        if c['subject'] in index:
            degree[index[c['subject']]] += 1
        if c['reference'] in index:
            degree[index[c['reference']]] += 1
        if c['subject'] in index and c['reference'] in index:
            i, j = index[c['subject']], index[c['reference']]
            pair_relations.setdefault((min(i, j), max(i, j)), []).append(c)
    domains = [(1 << len(ps))-1 for ps in poses]
    compatibility = [[None for j in range(n)] for i in range(n)]
    neighbors = [[] for i in range(n)]
    for i in range(n):
        for j in range(i+1, n):
            ij, ji = [0]*len(poses[i]), [0]*len(poses[j])
            rels = pair_relations.get((i, j), [])
            for a, ra in enumerate(rects[i]):
                for b, rb in enumerate(rects[j]):
                    if overlaps(ra, rb):
                        continue
                    good = True
                    for c in rels:
                        if c['subject'] == names[i]:
                            good = front(poses[i][a], poses[j][b], c)
                        else:
                            good = front(poses[j][b], poses[i][a], c)
                        if not good:
                            break
                    if good:
                        ij[a] |= 1 << b
                        ji[b] |= 1 << a
            if any(s != domains[j] for s in ij):
                compatibility[i][j], compatibility[j][i] = ij, ji
                neighbors[i].append(j)
                neighbors[j].append(i)

    def propagate(ds, changed):
        queue = deque(changed)
        pending = set(changed)
        while queue:
            j = queue.popleft()
            pending.discard(j)
            if not ds[j]:
                return False
            for i in neighbors[j]:
                old, new, bits = ds[i], 0, ds[i]
                supports = compatibility[i][j]
                while bits:
                    bit = bits & -bits
                    bits -= bit
                    if supports[bit.bit_length()-1] & ds[j]:
                        new |= bit
                if not new:
                    return False
                if new != old:
                    ds[i] = new
                    if i not in pending:
                        pending.add(i)
                        queue.append(i)
        return True

    access = []
    step = 0.25
    ax, ay = int(math.ceil(width/step)), int(math.ceil(depth/step))
    afull = (1 << (ax*ay))-1
    aleft = sum(1 << (y*ax) for y in range(ay))
    aright = aleft << (ax-1)
    for rule in spec.get('access_paths', []):
        clearance = rule.get('clearance', 0.15)
        region = regions[rule['from_region']]
        sources, static = 0, 0
        for y in range(ay):
            py = (y+0.5)*step
            for x in range(ax):
                px = (x+0.5)*step
                bit = 1 << (y*ax+x)
                if px < clearance or py < clearance or px > width-clearance or py > depth-clearance:
                    static |= bit
                if region['x'] <= px <= region['x']+region['width'] and region['y'] <= py <= region['y']+region['depth']:
                    sources |= bit
        for r in fixed_rects:
            static |= blocked_cells(r, clearance, ax, ay, step)
        amasks = [[blocked_cells(r, clearance, ax, ay, step) for r in rr] for rr in rects]
        target_name = rule['to_object']
        target_rects = [rectangle(fixed_map[target_name])] if target_name in fixed_map else rects[index[target_name]]
        distance = rule.get('interaction_distance', 0.8)
        targets = []
        for r in target_rects:
            target = 0
            for y in range(ay):
                py = (y+0.5)*step
                dy = max(r[1]-py, 0, py-r[3])
                for x in range(ax):
                    px = (x+0.5)*step
                    dx = max(r[0]-px, 0, px-r[2])
                    if dx*dx+dy*dy <= distance*distance+eps:
                        target |= 1 << (y*ax+x)
            targets.append(target)
        access.append((static, sources, amasks, target_name, targets))

    def accessible(choice):
        for static, sources, amasks, target_name, targets in access:
            blocked = static
            for i, v in enumerate(choice):
                blocked |= amasks[i][v]
            available = afull & ~blocked
            frontier = sources & available
            seen = frontier
            target = targets[0] if target_name in fixed_map else targets[choice[index[target_name]]]
            while frontier and not seen & target:
                frontier = (((frontier << 1) & ~aleft) | ((frontier >> 1) & ~aright) | (frontier << ax) | (frontier >> ax)) & available & ~seen
                seen |= frontier
            if not seen & target:
                return False
        return True

    learned, learned_paths = [], set()
    state = {'nodes': 0, 'best': None, 'score': -1, 'answer': None}

    def path_clause(path):
        clause = []
        for mm in masks:
            hits = 0
            for v, mask in enumerate(mm):
                if mask & path:
                    hits |= 1 << v
            clause.append(hits)
        return clause

    def enforce_clause(ds, clause):
        support = -1
        for i in range(n):
            hits = ds[i] & clause[i]
            if hits == ds[i]:
                return True, -1
            if hits:
                if support >= 0:
                    return True, -1
                support = i
        if support < 0:
            return False, -1
        ds[support] &= clause[support]
        return True, support

    def search(ds, changed, strict, limit):
        state['nodes'] += 1
        if state['nodes'] > limit or not propagate(ds, changed):
            return False
        while True:
            changes = []
            if strict:
                for clause in learned:
                    good, variable = enforce_clause(ds, clause)
                    if not good:
                        return False
                    if variable >= 0:
                        changes.append(variable)
            if changes:
                if not propagate(ds, changes):
                    return False
                continue
            blocked = fixed_blocked
            for i, bits in enumerate(ds):
                common = full
                while bits:
                    bit = bits & -bits
                    bits -= bit
                    common &= masks[i][bit.bit_length()-1]
                blocked |= common
            distance, path = navigation(blocked)
            if distance < 0:
                return False
            remaining = [i for i in range(n) if ds[i] & (ds[i]-1)]
            if not remaining:
                choice = [bits.bit_length()-1 for bits in ds]
                if accessible(choice):
                    if distance > state['score']:
                        state['best'], state['score'] = choice, distance
                    if distance >= minimum:
                        state['answer'] = choice
                        return True
                    if not strict:
                        return True
            if strict and distance < minimum:
                clause = path_clause(path)
                if path not in learned_paths and len(learned) < 96:
                    learned_paths.add(path)
                    learned.append(clause)
                good, variable = enforce_clause(ds, clause)
                if not good:
                    return False
                if variable >= 0:
                    if not propagate(ds, [variable]):
                        return False
                    continue
            break
        if not remaining:
            return False
        variable = min(remaining, key=lambda i: (ds[i].bit_count(), -degree[i], -len(neighbors[i])))
        values = []
        bits = ds[variable]
        while bits:
            bit = bits & -bits
            bits -= bit
            v = bit.bit_length()-1
            flexibility = 0
            for j in neighbors[variable]:
                if ds[j] & (ds[j]-1):
                    flexibility += (compatibility[variable][j][v] & ds[j]).bit_count()
            gain = (masks[variable][v] & path).bit_count()
            values.append((-gain if strict else 0, -flexibility, rects[variable][v], poses[variable][v]['yaw_deg'], bit))
        values.sort()
        for value in values:
            child = ds[:]
            child[variable] = value[-1]
            if search(child, [variable], strict, limit):
                return True
            if state['nodes'] >= limit:
                break
        return False

    if all(domains):
        search(domains[:], list(range(n)), True, 24000)
        if state['answer'] is None and state['best'] is None:
            state['nodes'] = 0
            search(domains[:], list(range(n)), False, 2000)
    choice = state['answer'] if state['answer'] is not None else state['best']
    objects = fixed[:]
    if choice is not None:
        objects.extend(poses[i][v] for i, v in enumerate(choice))
    else:
        for i, name in enumerate(names):
            if poses[i]:
                objects.append(poses[i][0])
            elif packet['domains'].get(name):
                objects.append(packet['domains'][name][0])
    return {'scene_id': spec['scene_id'], 'objects': objects}
