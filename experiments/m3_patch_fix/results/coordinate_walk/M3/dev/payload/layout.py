"""coordinate_walk initializer; fixed before policy creation."""
import math
import random

METHOD = 'repair'


def parameters(task, rng, heuristic=False):
    values,groups = {},{}
    for name,parent in task['supports'].items():
        if parent != 'floor':
            groups.setdefault(parent,[]).append(name)
    for name,parent in task['supports'].items():
        if parent == 'floor':
            x0,y0,x1,y1 = task['zones'][name]
            values[name] = [(x0+x1)/2,(y0+y1)/2,0.] if heuristic else [rng.uniform(x0,x1),rng.uniform(y0,y1),rng.uniform(-math.pi,math.pi)]
        else:
            mount = next(q for q in task['fixed'] if q['id'] == parent)
            a = task['spec']['assets'][mount['asset']]
            siblings = sorted(groups[parent])
            offset = (siblings.index(name)-(len(siblings)-1)/2)*a['width']/2
            values[name] = [offset,-.06,0.] if heuristic else [rng.uniform(-a['width']*.37,a['width']*.37),rng.uniform(-a['depth']*.25,a['depth']*.25),rng.uniform(-math.pi,math.pi)]
    return values


def clamp_floor(task, values, name):
    x0,y0,x1,y1 = task['zones'][name]
    a = next(q['asset'] for q in task['spec']['required_objects'] if q['id'] == name)
    a = task['spec']['assets'][a]
    x,y,yaw = values[name]
    ex = (abs(math.cos(yaw))*a['width']+abs(math.sin(yaw))*a['depth'])/2+.01
    ey = (abs(math.sin(yaw))*a['width']+abs(math.cos(yaw))*a['depth'])/2+.01
    values[name] = [min(x1-ex,max(x0+ex,x)),min(y1-ey,max(y0+ey,y)),yaw]


def best_index(history):
    best = 0
    for i,row in enumerate(history):
        if row['report']['optimizer_loss'] < history[best]['report']['optimizer_loss']-1e-8:
            best = i
    return best


def improve_floor_preference(task, values, name):
    if task['supports'][name] != 'floor':
        return
    assets = task['spec']['assets']
    heights = {q['id']: assets[q['asset']]['height'] / 2
               for q in task['spec']['required_objects']}
    relations = []
    for relation in task['relations']:
        if relation['a'] == name:
            other = relation['b']
        elif relation['b'] == name:
            other = relation['a']
        else:
            continue
        if other != name and task['supports'].get(other) == 'floor':
            relations.append((other, relation['target']))
    if not relations:
        return
    original = list(values[name])
    gx, gy, error = 0., 0., 0.
    for other, target in relations:
        dx = original[0] - values[other][0]
        dy = original[1] - values[other][1]
        dz = heights[name] - heights[other]
        distance = math.sqrt(dx*dx + dy*dy + dz*dz)
        residual = distance - target
        error += abs(residual)
        if distance > 1e-12 and abs(residual) > 1e-12:
            direction = 1. if residual > 0. else -1.
            gx += direction * dx / distance
            gy += direction * dy / distance
    norm = math.hypot(gx, gy)
    if norm <= 1e-12:
        return
    # Keep the random proposal's yaw and limit its translation correction to 6cm.
    # Backtracking accepts only a lower continuous centre-distance error.
    step = .06
    for _ in range(4):
        values[name] = [original[0] - step*gx/norm,
                        original[1] - step*gy/norm, original[2]]
        clamp_floor(task, values, name)
        candidate = values[name]
        displacement = math.hypot(candidate[0] - original[0],
                                  candidate[1] - original[1])
        candidate_error = 0.
        for other, target in relations:
            dx = candidate[0] - values[other][0]
            dy = candidate[1] - values[other][1]
            dz = heights[name] - heights[other]
            candidate_error += abs(math.sqrt(dx*dx + dy*dy + dz*dz) - target)
        if displacement <= .06 + 1e-12 and candidate_error < error - 1e-12:
            return
        step *= .5
    values[name] = original


def layout(packet):
    history = packet['history']
    if packet['phase'] == 'final':
        return {'index': best_index(history)}
    task, index = packet['task'], packet['query']
    rng = random.Random(packet['seed'])
    if index:
        raw = packet['memory']['rng']
        rng.setstate((raw[0], tuple(raw[1]), raw[2]))
        current = history[best_index(history)]['values']
        values = {name:list(v) for name,v in current.items()}
        name = rng.choice(list(values))
        selected = name
        scale = .35 if task['supports'][name] == 'floor' else .12
        values[name][0] += rng.uniform(-scale, scale)
        values[name][1] += rng.uniform(-scale, scale)
        values[name][2] += rng.uniform(-.4, .4)
    else:
        values = parameters(task, rng, True)
    for name in task['zones']:
        clamp_floor(task, values, name)
    if index:
        # The clamping loop rebinds name; recover the single perturbed object.
        improve_floor_preference(task, values, selected)
    return {'values': values, 'memory': {'rng': rng.getstate()}}
