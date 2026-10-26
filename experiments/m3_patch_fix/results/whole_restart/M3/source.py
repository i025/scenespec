"""whole_restart initializer; fixed before policy creation."""
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


def improve_floor_relations(task, values):
    """Take one bounded descent step without changing yaw or random draws."""
    assets = task['spec']['assets']
    heights = {
        obj['id']: assets[obj['asset']]['height']/2+.003
        for obj in task['spec']['required_objects']
        if task['supports'].get(obj['id']) == 'floor'
    }
    relations = [r for r in task['relations']
                 if r['a'] in heights and r['b'] in heights]
    if not relations:
        return

    def error(candidate):
        total = 0.
        for r in relations:
            a, b = r['a'], r['b']
            distance = math.sqrt(sum((candidate[a][i]-candidate[b][i])**2
                                     for i in (0, 1))
                                 +(heights[a]-heights[b])**2)
            total += abs(distance-r['target'])
        return total

    direction = {name: [0., 0.] for name in heights}
    for r in relations:
        a, b = r['a'], r['b']
        dx, dy = values[b][0]-values[a][0], values[b][1]-values[a][1]
        distance = math.sqrt(dx*dx+dy*dy+(heights[a]-heights[b])**2)
        if distance <= 1e-12:
            continue
        residual = distance-r['target']
        for i, delta in enumerate((dx, dy)):
            correction = .5*residual*delta/distance
            direction[a][i] += correction
            direction[b][i] -= correction
    for shift in direction.values():
        length = math.hypot(*shift)
        if length > .06:
            shift[0] *= .06/length
            shift[1] *= .06/length

    before = error(values)
    for scale in (1., .5, .25, .125):
        candidate = {name: list(pose) for name, pose in values.items()}
        feasible = True
        for name, shift in direction.items():
            candidate[name][0] += scale*shift[0]
            candidate[name][1] += scale*shift[1]
            clamp_floor(task, candidate, name)
            # An empty footprint interval cannot establish containment.
            x0, y0, x1, y1 = task['zones'][name]
            obj = next(q for q in task['spec']['required_objects']
                       if q['id'] == name)
            asset = assets[obj['asset']]
            x, y, yaw = candidate[name]
            ex = (abs(math.cos(yaw))*asset['width']
                  +abs(math.sin(yaw))*asset['depth'])/2+.01
            ey = (abs(math.sin(yaw))*asset['width']
                  +abs(math.cos(yaw))*asset['depth'])/2+.01
            if not (x0+ex <= x <= x1-ex and y0+ey <= y <= y1-ey):
                feasible = False
            if math.hypot(x-values[name][0], y-values[name][1]) > .06+1e-12:
                feasible = False
        if feasible and error(candidate) < before-1e-12:
            values.update(candidate)
            return


def best_index(history):
    best = 0
    for i,row in enumerate(history):
        if row['report']['optimizer_loss'] < history[best]['report']['optimizer_loss']-1e-8:
            best = i
    return best


def layout(packet):
    history = packet['history']
    if packet['phase'] == 'final':
        return {'index': best_index(history)}
    task, index = packet['task'], packet['query']
    rng = random.Random(packet['seed'])
    if index:
        raw = packet['memory']['rng']
        rng.setstate((raw[0], tuple(raw[1]), raw[2]))
    values = parameters(task, rng, index == 0)
    for name in task['zones']:
        clamp_floor(task, values, name)
    improve_floor_relations(task, values)
    return {'values': values, 'memory': {'rng': rng.getstate()}}
