"""State-machine transcription of the frozen baseline; task transforms stay external."""
import math
import random

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


def improve_distance(task, values, rule, rng):
    """Project a floor pair toward its preferred 3D centre distance."""
    a,b = rule['a'],rule['b']
    assets = task['spec']['assets']
    objects = {q['id']:q['asset'] for q in task['spec']['required_objects']}
    dz = (assets[objects[a]]['height']-assets[objects[b]]['height'])/2
    target = math.sqrt(max(0.,rule['target']**2-dz**2))
    # Random sharing retains different positions along the same distance contour.
    share = rng.uniform(.2,.8)
    angle = rng.uniform(-math.pi,math.pi)
    for _ in range(6):
        dx = values[b][0]-values[a][0]
        dy = values[b][1]-values[a][1]
        distance = math.hypot(dx,dy)
        error = distance-target
        if abs(error) < 1e-5:
            break
        if distance > 1e-8:
            ux,uy = dx/distance,dy/distance
        else:
            ux,uy = math.cos(angle),math.sin(angle)
        # Repeated projection lets the free endpoint absorb a clamped move.
        for name,amount in ((a,share*error),(b,-(1-share)*error)):
            values[name][0] += amount*ux
            values[name][1] += amount*uy
            clamp_floor(task,values,name)


def layout(packet):
    history = packet['history']
    if packet['phase'] == 'final':
        return {'index':best_index(history)}
    task,index = packet['task'],packet['query']
    rng = random.Random(packet['seed'])
    if index:
        raw = packet['memory']['rng']
        rng.setstate((raw[0],tuple(raw[1]),raw[2]))
        best = history[best_index(history)]
        current = best['values']
        best_report = best['report']
    else:
        current = parameters(task,rng,True)
        for name in task['zones']:
            clamp_floor(task,current,name)
    candidate = {name:list(values) for name,values in current.items()}
    if index:
        names = list(candidate)
        failed = [name for f in best_report['failures'] for name in f['objects'] if name in candidate]
        floor_rules = [rule for rule in task['relations']
                       if rule['a'] != rule['b']
                       and task['supports'].get(rule['a']) == 'floor'
                       and task['supports'].get(rule['b']) == 'floor'
                       and rule['a'] in task['zones']
                       and rule['b'] in task['zones']]
        if floor_rules and index%3 == 1:
            improve_distance(task,candidate,rng.choice(floor_rules),rng)
        else:
            name = rng.choice(failed or names)
            candidate[name] = parameters(task,rng)[name]
            if name in task['zones']:
                clamp_floor(task,candidate,name)
    return {'values':candidate,'memory':{'rng':rng.getstate()}}
