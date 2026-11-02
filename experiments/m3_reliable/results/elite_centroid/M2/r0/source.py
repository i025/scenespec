import math
import random

METHOD = 'repair'


def parameters(task, rng, yaws):
    values, groups = {}, {}
    for name, parent in task['supports'].items():
        if parent != 'floor':
            groups.setdefault(parent, []).append(name)
    for name, parent in task['supports'].items():
        if parent == 'floor':
            x0, y0, x1, y1 = task['zones'][name]
            values[name] = [rng.uniform(x0, x1), rng.uniform(y0, y1), yaws[name]]
        else:
            mount = next(q for q in task['fixed'] if q['id'] == parent)
            asset = task['spec']['assets'][mount['asset']]
            siblings = sorted(groups[parent])
            offset = (siblings.index(name) - (len(siblings) - 1) / 2) * asset['width'] * .48
            values[name] = [offset + rng.uniform(-.08, .08), rng.uniform(-.14, .02), yaws[name]]
    return values


def clamp_floor(task, values, name):
    x0, y0, x1, y1 = task['zones'][name]
    asset = next(q['asset'] for q in task['spec']['required_objects'] if q['id'] == name)
    asset = task['spec']['assets'][asset]
    x, y, yaw = values[name]
    ex = (abs(math.cos(yaw)) * asset['width'] + abs(math.sin(yaw)) * asset['depth']) / 2 + .01
    ey = (abs(math.sin(yaw)) * asset['width'] + abs(math.cos(yaw)) * asset['depth']) / 2 + .01
    values[name] = [min(x1 - ex, max(x0 + ex, x)), min(y1 - ey, max(y0 + ey, y)), yaw]


def best_index(history):
    return min(range(len(history)), key=lambda i: history[i]['report']['optimizer_loss'])


def refine_floor_relations(task, values):
    """Reduce centre-distance error with bounded, yaw-preserving moves."""
    assets = task['spec']['assets']
    heights = {q['id']: assets[q['asset']]['height'] / 2 + .003
               for q in task['spec']['required_objects']}
    for relation in task['relations']:
        a, b = relation['a'], relation['b']
        if (a == b or task['supports'].get(a) != 'floor'
                or task['supports'].get(b) != 'floor'):
            continue
        if a not in task['zones'] or b not in task['zones']:
            continue
        old_a, old_b = values[a][:], values[b][:]
        dx, dy = old_b[0] - old_a[0], old_b[1] - old_a[1]
        planar = math.hypot(dx, dy)
        if planar < 1e-9:
            continue
        dz = heights[b] - heights[a]
        distance = math.hypot(planar, dz)
        target = max(0., relation['target'])
        desired = math.sqrt(max(0., target * target - dz * dz))
        # Keep the pair midpoint and its seed-dependent direction whenever
        # bounds permit. A trust radius limits changes to the search proposal.
        step = max(-.18, min(.18, .375 * (planar - desired)))
        sx, sy = step * dx / planar, step * dy / planar
        values[a] = [old_a[0] + sx, old_a[1] + sy, old_a[2]]
        values[b] = [old_b[0] - sx, old_b[1] - sy, old_b[2]]
        clamp_floor(task, values, a)
        clamp_floor(task, values, b)
        after = math.hypot(math.hypot(values[b][0] - values[a][0],
                                     values[b][1] - values[a][1]), dz)
        if abs(after - target) >= abs(distance - target):
            values[a], values[b] = old_a, old_b


def layout(packet):
    history=packet['history']
    if packet['phase']=='final':return {'index':best_index(history)}
    task=packet['task'];rng=random.Random(packet['seed'])
    if packet['query']:
        raw=packet['memory']['rng'];rng.setstate((raw[0],tuple(raw[1]),raw[2]))
        yaws=packet['memory']['yaws']
    else:yaws={name:rng.uniform(-.25,.25) for name in task['supports']}
    if packet['query']<8:
        values=parameters(task,rng,yaws)
    else:
        elite=sorted(history,key=lambda row:row['report']['optimizer_loss'])[:3]
        values={name:[sum(row['values'][name][axis] for row in elite)/len(elite) for axis in range(3)] for name in task['supports']}
        for name in values:
            scale=(.18 if task['supports'][name]=='floor' else .025)*(1.-packet['query']/32.)
            values[name][0]+=rng.uniform(-scale,scale)
            values[name][1]+=rng.uniform(-scale,scale)
    for name in task['zones']:clamp_floor(task,values,name)
    refine_floor_relations(task,values)
    return {'values':values,'memory':{'rng':rng.getstate(),'yaws':yaws}}
