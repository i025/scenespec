from collections import deque
import math

def layout(packet):
    spec = packet['spec']
    width, depth = packet['width'], packet['depth']
    assets = spec['assets']
    fixed = list(packet['fixed'])
    fixed_ids = {o['id'] for o in fixed}
    required = {o['id']: o['asset'] for o in spec['required_objects']}
    constraints = spec.get('constraints', [])
    regions = {r['id']: r for r in spec.get('regions', [])}
    relations = [c for c in constraints if c['type'] == 'in_front_of']
    directional = {c['reference'] for c in relations}
    eps = 1e-8

    def rectangle(o):
        a = assets[o['asset']]
        w, d = a['width'], a['depth']
        if o['yaw_deg'] % 180:
            w, d = d, w
        return (o['x']-w/2, o['y']-d/2, o['x']+w/2, o['y']+d/2)

    def overlaps(a, b):
        return min(a[2], b[2])-max(a[0], b[0]) > eps and min(a[3], b[3])-max(a[1], b[1]) > eps

    keep = []
    for c in constraints:
        if c['type'] == 'keep_clear':
            r = regions[c['region']]
            keep.append((r['x'], r['y'], r['x']+r['width'], r['y']+r['depth']))

    def unary(o, r):
        if r[0] < -eps or r[1] < -eps or r[2] > width+eps or r[3] > depth+eps:
            return False
        if any(overlaps(r, z) for z in keep):
            return False
        for c in constraints:
            if c['type'] == 'near_wall' and c['subject'] == o['id']:
                gap = {'west': r[0], 'south': r[1], 'east': width-r[2], 'north': depth-r[3]}[c['wall']]
                if gap < -eps or gap > c['max_gap']+eps:
                    return False
        return True

    def front(c, a, ar, b, br):
        fx, fy = {0:(0,1), 90:(-1,0), 180:(0,-1), 270:(1,0)}[b['yaw_deg'] % 360]
        dx, dy = a['x']-b['x'], a['y']-b['y']
        aw, ad = (ar[2]-ar[0])/2, (ar[3]-ar[1])/2
        bw, bd = (br[2]-br[0])/2, (br[3]-br[1])/2
        gap = dx*fx+dy*fy-abs(fx)*(aw+bw)-abs(fy)*(ad+bd)
        lateral = abs(dx*fy-dy*fx)
        return -eps <= gap <= c['max_gap']+eps and lateral <= abs(fy)*(aw+bw)+abs(fx)*(ad+bd)+eps

    def compatible(a, ar, b, br):
        if overlaps(ar, br):
            return False
        for c in relations:
            if c['subject'] == a['id'] and c['reference'] == b['id']:
                if not front(c, a, ar, b, br):
                    return False
            elif c['subject'] == b['id'] and c['reference'] == a['id']:
                if not front(c, b, br, a, ar):
                    return False
        return True

    fr = [rectangle(o) for o in fixed]
    names = [name for name in required if name not in fixed_ids]
    poses, rects = [], []
    for name in names:
        pp, rr, seen = [], [], set()
        for o in packet['domains'].get(name, []):
            if o['id'] != name or o['asset'] != required[name]:
                continue
            r = rectangle(o)
            key = r + ((o['yaw_deg'] % 360,) if name in directional else ())
            if key in seen or not unary(o, r):
                continue
            if not all(compatible(o, r, f, z) for f, z in zip(fixed, fr)):
                continue
            seen.add(key)
            pp.append(o)
            rr.append(r)
        poses.append(pp)
        rects.append(rr)
    n = len(names)
    fallback = fixed + [poses[i][0] if poses[i] else packet['domains'][names[i]][0] for i in range(n)]
    if any(not pp for pp in poses):
        return {'scene_id': spec['scene_id'], 'objects': fallback}

    gw, gh = int(width), int(depth)
    total = gw*gh
    universe = (1 << total)-1
    left = sum(1 << (y*gw) for y in range(gh))
    right = left << (gw-1)
    sx, sy = packet['start']
    gx, gy = packet['goal']
    startbit = 1 << (sy*gw+sx)
    goalbit = 1 << (gy*gw+gx)
    minimum = packet['minimum_steps']
    effort = [0]
    limit = 1600000

    def neighbors(bits):
        return (((bits & ~right) << 1) | ((bits & ~left) >> 1) | (bits << gw) | (bits >> gw)) & universe

    def mask_rect(r):
        bits = 0
        for y in range(max(0, int(math.floor(r[1]-.65))+1), min(gh, int(math.ceil(r[3]-.35)))):
            cy = y+.5
            if not r[1]-.15 < cy < r[3]+.15:
                continue
            for x in range(max(0, int(math.floor(r[0]-.65))+1), min(gw, int(math.ceil(r[2]-.35)))):
                if r[0]-.15 < x+.5 < r[2]+.15:
                    bits |= 1 << (y*gw+x)
        return bits

    fixedmask = 0
    for r in fr:
        fixedmask |= mask_rect(r)
    blocks = [[mask_rect(r) for r in rr] for rr in rects]
    initial = []
    for i in range(n):
        initial.append(sum(1 << a for a, b in enumerate(blocks[i]) if not b & (startbit | goalbit)))

    def walk(blocked, path=False):
        if blocked & (startbit | goalbit):
            return -1, 0
        free = universe & ~blocked
        wave = startbit
        visited = wave
        layers = [wave] if path else []
        distance = 0
        while wave:
            effort[0] += 1
            if wave & goalbit:
                if not path:
                    return distance, 0
                bit = goalbit
                route = bit
                for layer in reversed(layers[:-1]):
                    options = neighbors(bit) & layer
                    bit = options & -options
                    route |= bit
                return distance, route
            wave = neighbors(wave) & free & ~visited
            visited |= wave
            distance += 1
            if path:
                layers.append(wave)
        return -1, 0

    support = [[None for j in range(n)] for i in range(n)]
    adjacent = [[] for i in range(n)]
    for i in range(n):
        for j in range(i+1, n):
            ij = [0]*len(poses[i])
            ji = [0]*len(poses[j])
            for a, o in enumerate(poses[i]):
                for b, q in enumerate(poses[j]):
                    if compatible(o, rects[i][a], q, rects[j][b]):
                        ij[a] |= 1 << b
                        ji[b] |= 1 << a
            support[i][j], support[j][i] = ij, ji
            if any(v != (1 << len(poses[j]))-1 for v in ij):
                adjacent[i].append(j)
                adjacent[j].append(i)

    def propagate(ds, seeds):
        queue = deque(seeds)
        queued = set(seeds)
        while queue:
            j = queue.popleft()
            queued.discard(j)
            if not ds[j]:
                return False
            for i in adjacent[j]:
                old = ds[i]
                rem = old
                new = 0
                table = support[i][j]
                while rem:
                    bit = rem & -rem
                    rem -= bit
                    effort[0] += 1
                    if table[bit.bit_length()-1] & ds[j]:
                        new |= bit
                if not new:
                    return False
                if new != old:
                    ds[i] = new
                    if i not in queued:
                        queue.append(i)
                        queued.add(i)
        return True

    access = spec.get('access_paths', [])
    access_cache = {}

    def access_ok(indices):
        if not access:
            return True
        objects = fixed + [poses[i][indices[i]] for i in range(n)]
        rectangles = fr + [rects[i][indices[i]] for i in range(n)]
        object_rects = {o['id']: r for o, r in zip(objects, rectangles)}
        signature = tuple(sorted(rectangles))
        if signature in access_cache:
            return access_cache[signature]
        step = .1
        aw, ah = int(math.ceil(width/step)), int(math.ceil(depth/step))
        allbits = (1 << (aw*ah))-1
        edgeleft = sum(1 << (y*aw) for y in range(ah))
        edgeright = edgeleft << (aw-1)
        for ap in access:
            clearance = ap.get('clearance', .15)
            blocked = 0
            for y in range(ah):
                cy = (y+.5)*step
                row = 0
                for x in range(aw):
                    cx = (x+.5)*step
                    if cx < clearance or cy < clearance or cx > width-clearance or cy > depth-clearance:
                        row |= 1 << x
                blocked |= row << (y*aw)
            for r in rectangles:
                lo = max(0, int(math.floor((r[0]-clearance)/step-.5))+1)
                hi = min(aw, int(math.ceil((r[2]+clearance)/step-.5)))
                bot = max(0, int(math.floor((r[1]-clearance)/step-.5))+1)
                top = min(ah, int(math.ceil((r[3]+clearance)/step-.5)))
                if hi > lo:
                    row = ((1 << (hi-lo))-1) << lo
                    for y in range(bot, top):
                        blocked |= row << (y*aw)
            region = regions[ap['from_region']]
            target = object_rects[ap['to_object']]
            reach = 0
            targets = 0
            radius = ap.get('interaction_distance', .8)
            for y in range(ah):
                cy = (y+.5)*step
                for x in range(aw):
                    cx = (x+.5)*step
                    if region['x'] <= cx <= region['x']+region['width'] and region['y'] <= cy <= region['y']+region['depth']:
                        reach |= 1 << (y*aw+x)
                    dx = max(target[0]-cx, 0, cx-target[2])
                    dy = max(target[1]-cy, 0, cy-target[3])
                    if dx*dx+dy*dy <= radius*radius+eps:
                        targets |= 1 << (y*aw+x)
            free = allbits & ~blocked
            reach &= free
            wave = reach
            while wave and not reach & targets:
                effort[0] += 1
                wave = (((wave & ~edgeright) << 1) | ((wave & ~edgeleft) >> 1) | (wave << aw) | (wave >> aw)) & free & ~reach
                reach |= wave
            if not reach & targets:
                if len(access_cache) < 3000:
                    access_cache[signature] = False
                return False
        if len(access_cache) < 3000:
            access_cache[signature] = True
        return True

    failures = set()
    best = [None, -1]
    nodes = [0]

    def search(ds):
        nodes[0] += 1
        if effort[0] > limit or nodes[0] > 45000:
            return None
        while True:
            blocked = fixedmask
            possible = fixedmask
            active = []
            for i in range(n):
                rem = ds[i]
                common = universe
                union = 0
                while rem:
                    bit = rem & -rem
                    rem -= bit
                    b = blocks[i][bit.bit_length()-1]
                    common &= b
                    union |= b
                blocked |= common
                possible |= union
                if ds[i] & (ds[i]-1):
                    active.append(i)
            distance, route = walk(blocked, True)
            if distance < 0:
                return None
            if not active:
                indices = [d.bit_length()-1 for d in ds]
                if blocked in failures:
                    return None
                if distance >= minimum:
                    if access_ok(indices):
                        return indices
                elif distance > best[1] and access_ok(indices):
                    best[0], best[1] = indices, distance
                if len(failures) < 30000:
                    failures.add(blocked)
                return None
            hits = {}
            if distance < minimum:
                for i in active:
                    rem = ds[i]
                    h = 0
                    while rem:
                        bit = rem & -rem
                        rem -= bit
                        if blocks[i][bit.bit_length()-1] & route:
                            h |= bit
                    if h:
                        hits[i] = h
                if not hits:
                    return None
                if len(hits) == 1:
                    i = next(iter(hits))
                    ds[i] = hits[i]
                    if not propagate(ds, [i]):
                        return None
                    continue
                if len(active) >= 3:
                    upper, unused = walk(possible)
                    if 0 <= upper < minimum:
                        return None
            break
        variable = min(active, key=lambda i: (ds[i].bit_count(), -len(adjacent[i]), -int(i in hits)))
        choices = []
        rem = ds[variable]
        while rem:
            bit = rem & -rem
            rem -= bit
            a = bit.bit_length()-1
            dist, unused = walk(blocked | blocks[variable][a])
            if dist < 0:
                continue
            freedom = sum((support[variable][j][a] & ds[j]).bit_count() for j in adjacent[variable])
            hit = (blocks[variable][a] & route).bit_count() if distance < minimum else 0
            choices.append((dist >= minimum, dist, hit, freedom, bit))
        choices.sort(reverse=True)
        for unused1, unused2, unused3, unused4, bit in choices:
            child = ds[:]
            child[variable] = bit
            if propagate(child, [variable]):
                answer = search(child)
                if answer is not None:
                    return answer
            if effort[0] > limit or nodes[0] > 45000:
                break
        return None

    answer = None
    if all(initial) and propagate(initial, range(n)):
        answer = search(initial)
    if answer is None:
        answer = best[0]
    if answer is not None:
        fallback = fixed + [poses[i][answer[i]] for i in range(n)]
    return {'scene_id': spec['scene_id'], 'objects': fallback}
