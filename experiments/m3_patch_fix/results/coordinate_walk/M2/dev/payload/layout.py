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


def guide_floor_relation(task, values, name):
    """Reduce radial preference error without changing sampled yaw or tangent."""
    if task['supports'][name] != 'floor':
        return
    assets = task['spec']['assets']
    heights = {q['id']: assets[q['asset']]['height']/2+.003
               for q in task['spec']['required_objects']}
    dx_total,dy_total,count = 0.,0.,0
    x,y = values[name][:2]
    for relation in task['relations']:
        if name == relation['a']:
            other = relation['b']
        elif name == relation['b']:
            other = relation['a']
        else:
            continue
        if task['supports'].get(other) != 'floor':
            continue
        dx,dy = x-values[other][0],y-values[other][1]
        radius = math.hypot(dx,dy)
        if radius < 1e-9:
            continue
        target = max(relation['min'],min(relation['max'],relation['target']))
        dz = heights[name]-heights[other]
        desired = math.sqrt(max(0.,target*target-dz*dz))
        step = max(-.25,min(.25,.65*(desired-radius)))
        dx_total += step*dx/radius
        dy_total += step*dy/radius
        count += 1
    if count:
        values[name][0] += dx_total/count
        values[name][1] += dy_total/count


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
        current = history[best_index(history)]['values']
        values = {name:list(v) for name,v in current.items()}
        name = rng.choice(list(values))
        scale = .35 if task['supports'][name] == 'floor' else .12
        values[name][0] += rng.uniform(-scale, scale)
        values[name][1] += rng.uniform(-scale, scale)
        values[name][2] += rng.uniform(-.4, .4)
        guide_floor_relation(task, values, name)
    else:
        values = parameters(task, rng, True)
    for name in task['zones']:
        clamp_floor(task, values, name)
    return {'values': values, 'memory': {'rng': rng.getstate()}}
