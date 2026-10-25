"""Simple deterministic depth-first constraint search, capped at 2,000 nodes.

Pure standalone baseline; only executed by the restricted runner. No oracle.
"""
import math
from collections import deque


def box(packet, p):
    a = packet['spec']['assets'][p['asset']]
    w, d = a['width'], a['depth']
    if p['yaw_deg']%180:
        w, d = d, w
    return (p['x']-w/2, p['y']-d/2, p['z'], p['x']+w/2, p['y']+d/2, p['z']+a['height'])


def local(packet, p, placed):
    b = box(packet, p)
    rule = packet['requirements'][p['id']]
    r = packet['rooms'][rule['room']]
    if not (r[0]-1e-7 <= b[0] and r[1]-1e-7 <= b[1] and b[3] <= r[2]+1e-7 and b[4] <= r[3]+1e-7):
        return False
    if not rule['height'][0]-1e-7 <= b[2] <= rule['height'][1]+1e-7 or b[2] < 0 or b[5] > packet['spec']['room']['height']+1e-7:
        return False
    if any(all(min(a[i+3], b[i+3])-max(a[i], b[i]) > 1e-7 for i in range(3))
           for a in [box(packet, q) for q in placed.values()]):
        return False
    for relation in packet['distances']:
        other = relation['b'] if relation['a'] == p['id'] else relation['a'] if relation['b'] == p['id'] else None
        if other in placed:
            a = box(packet, placed[other])
            distance = math.sqrt(sum(((a[i]+a[i+3]-b[i]-b[i+3])/2)**2 for i in range(3)))
            if not relation['min']-1e-7 <= distance <= relation['max']+1e-7:
                return False
    parent = rule['support']
    if parent == 'floor':
        return abs(b[2]) < 1e-7
    if parent not in placed:
        return False
    a = box(packet, placed[parent])
    return abs(b[2]-a[5]) <= 1e-7 and a[0]+.05-1e-7 <= b[0] and a[1]+.05-1e-7 <= b[1] and b[3] <= a[3]-.05+1e-7 and b[4] <= a[4]-.05+1e-7


def ray(origin, target, b):
    lo, hi = 0., 1.-1e-6
    for i in range(3):
        delta = target[i]-origin[i]
        if abs(delta) < 1e-10:
            if not b[i] <= origin[i] <= b[i+3]:
                return False
        else:
            a, c = (b[i]-origin[i])/delta, (b[i+3]-origin[i])/delta
            lo, hi = max(lo, min(a, c)), min(hi, max(a, c))
            if lo > hi:
                return False
    return True


def global_ok(packet, placed):
    boxes = {key:box(packet, p) for key, p in placed.items()}
    view = packet['view']
    origin = view['position']
    forward = [b-a for a, b in zip(origin, view['target'])]
    norm = math.sqrt(sum(v*v for v in forward))
    forward = [v/norm for v in forward]
    norm = math.hypot(forward[0], forward[1])
    right = [forward[1]/norm, -forward[0]/norm, 0.]
    up = [right[1]*forward[2], -right[0]*forward[2], right[0]*forward[1]-right[1]*forward[0]]
    tangent = math.tan(math.radians(view['fov']/2))
    for name, limits in packet['visibility'].items():
        b, count = boxes[name], 0
        for fx in (.15, .5, .85):
            for fy in (.15, .5, .85):
                for fz in (.15, .5, .85):
                    point = [b[i]+f*(b[i+3]-b[i]) for i, f in enumerate((fx, fy, fz))]
                    delta = [b-a for a, b in zip(origin, point)]
                    depth = sum(a*b for a, b in zip(delta, forward))
                    framed = depth > 0 and all(abs(sum(a*b for a, b in zip(delta, axis))) <= depth*tangent for axis in (right, up))
                    if framed and not any(ray(origin, point, other) for key, other in boxes.items() if key != name):
                        count += 1
        if not limits[0]-1e-7 <= count/27 <= limits[1]+1e-7:
            return False
    free = {(x, y) for x in range(2*packet['width']-1) for y in range(2*packet['depth']-1)
            if not any(b[2] < 1.8-1e-7 and b[5] > 1e-7 and b[0]-.2 < x*.5+.5 < b[3]+.2 and b[1]-.2 < y*.5+.5 < b[4]+.2 for b in boxes.values())}
    start, goal = tuple(2*v for v in packet['start']), tuple(2*v for v in packet['goal'])
    if start not in free or goal not in free:
        return False
    pending, seen = deque([start]), {start}
    while pending:
        cell = pending.popleft()
        if cell == goal:
            return True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (cell[0]+dx, cell[1]+dy)
            if q in free and q not in seen:
                seen.add(q)
                pending.append(q)
    return False


def answer(packet, placed):
    return {'state':{'scene_id':packet['spec']['scene_id'],
                     'objects':[{k:v for k, v in p.items() if k != 'z'} for p in placed.values()]},
            'elevations':{key:p['z'] for key, p in placed.items()}}


def layout(packet):
    placed = {p['id']:p for p in packet['fixed']}
    names = sorted(packet['domains'], key=lambda k:(packet['requirements'][k]['height'][0], k))
    count = [0]
    def search(i):
        if i == len(names):
            return dict(placed) if global_ok(packet, placed) else None
        name = names[i]
        for p in packet['domains'][name]:
            count[0] += 1
            if count[0] > 2000:
                return None
            if local(packet, p, placed):
                placed[name] = p
                found = search(i+1)
                if found is not None:
                    return found
                del placed[name]
        return None
    found = search(0)
    return answer(packet, found if found is not None else placed)
