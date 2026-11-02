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


def refine_relations(task, values, rng):
    assets = {q['id']: task['spec']['assets'][q['asset']]
              for q in task['spec']['required_objects']}
    # Preserve each pair's midpoint and direction while reducing distance error.
    # Repeated projection redistributes motion when a zone blocks one endpoint.
    for _ in range(8):
        for relation in task['relations']:
            a, b = relation['a'], relation['b']
            if task['supports'].get(a) != 'floor' or task['supports'].get(b) != 'floor' or a == b:
                continue
            dx = values[b][0] - values[a][0]
            dy = values[b][1] - values[a][1]
            distance = math.hypot(dx, dy)
            dz = (assets[b]['height'] - assets[a]['height']) / 2
            target = min(relation['max'], max(relation['min'], relation['target']))
            planar_target = math.sqrt(max(0., target * target - dz * dz))
            if distance > 1e-12:
                ux, uy = dx / distance, dy / distance
            else:
                angle = rng.uniform(-math.pi, math.pi)
                ux, uy = math.cos(angle), math.sin(angle)
            step = (distance - planar_target) / 2
            values[a][0] += step * ux
            values[a][1] += step * uy
            values[b][0] -= step * ux
            values[b][1] -= step * uy
            clamp_floor(task, values, a)
            clamp_floor(task, values, b)


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
        parent=rng.choice(elite)
        values={name:list(parent['values'][name]) for name in task['supports']}
        for name in values:
            scale=(.18 if task['supports'][name]=='floor' else .025)*(1.-packet['query']/32.)
            values[name][0]+=rng.uniform(-scale,scale)
            values[name][1]+=rng.uniform(-scale,scale)
    for name in task['zones']:clamp_floor(task,values,name)
    if packet['query']>=8:refine_relations(task,values,rng)
    return {'values':values,'memory':{'rng':rng.getstate(),'yaws':yaws}}
