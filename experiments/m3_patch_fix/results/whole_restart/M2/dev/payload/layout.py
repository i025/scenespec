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


def refine_relations(task, values):
    """Make a small preference step without worsening floor relation bounds."""
    assets = task['spec']['assets']
    heights = {q['id']: assets[q['asset']]['height']/2+.003
               for q in task['spec']['required_objects']
               if task['supports'].get(q['id']) == 'floor'}
    relations = [r for r in task['relations']
                 if r['a'] in heights and r['b'] in heights]
    origin = {name: values[name][:] for name in heights}

    def errors(candidate):
        hard, preference = [], 0.
        for relation in relations:
            a, b = relation['a'], relation['b']
            distance = math.sqrt(
                (candidate[a][0]-candidate[b][0])**2
                +(candidate[a][1]-candidate[b][1])**2
                +(heights[a]-heights[b])**2)
            hard.append(max(0., relation['min']-distance,
                            distance-relation['max']))
            preference += abs(distance-relation['target'])
        return hard, preference

    hard, preference = errors(values)
    # Keep each random proposal close to its original sample; do not converge
    # all restarts to the same preferred-distance configuration.
    for relation in relations:
        a, b = relation['a'], relation['b']
        dx, dy = values[b][0]-values[a][0], values[b][1]-values[a][1]
        horizontal = math.hypot(dx, dy)
        if horizontal < 1e-12 or a == b:
            continue
        dz = heights[b]-heights[a]
        target = math.sqrt(max(0., relation['target']**2-dz**2))
        step = (horizontal-target)/2
        candidate = {name: value[:] for name, value in values.items()}
        for name, direction in ((a, 1.), (b, -1.)):
            x = values[name][0]+direction*step*dx/horizontal
            y = values[name][1]+direction*step*dy/horizontal
            ox, oy = origin[name][:2]
            displacement = math.hypot(x-ox, y-oy)
            scale = min(1., .04/max(displacement, 1e-12))
            candidate[name][:2] = [ox+(x-ox)*scale, oy+(y-oy)*scale]
            clamp_floor(task, candidate, name)
        new_hard, new_preference = errors(candidate)
        if (all(new <= old for new, old in zip(new_hard, hard))
                and new_preference < preference-1e-12):
            values = candidate
            hard, preference = new_hard, new_preference
    return values


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
    values = refine_relations(task, values)
    return {'values': values, 'memory': {'rng': rng.getstate()}}
