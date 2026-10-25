"""State-machine transcription of the frozen baseline; task transforms stay external."""
import math
import random

METHOD = 'joint'


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


def refine_relation(task, values, rng):
    # Floor centres have fixed heights; solve their preferred 3D separation
    # while projecting each endpoint back into its footprint-safe zone.
    rules = [rule for rule in task['relations']
             if rule['a'] in task['zones'] and rule['b'] in task['zones']]
    if not rules:
        return
    rule = rng.choice(rules)
    a,b = rule['a'],rule['b']
    assets = task['spec']['assets']
    objects = {q['id']: assets[q['asset']]
               for q in task['spec']['required_objects']}
    dz = (objects[b]['height']-objects[a]['height'])/2
    target = math.sqrt(max(0.,rule['target']**2-dz**2))
    # Vary which endpoint supplies most of the motion, preserving seeded
    # placement diversity along the set of preferred-distance solutions.
    share = rng.uniform(.25,.75)
    angle = rng.uniform(-math.pi,math.pi)
    best = {name:list(values[name]) for name in (a,b)}
    best_error = abs(math.hypot(values[b][0]-values[a][0],
                                values[b][1]-values[a][1],dz)-rule['target'])
    for _ in range(12):
        dx,dy = (values[b][j]-values[a][j] for j in (0,1))
        distance = math.hypot(dx,dy)
        if abs(distance-target) < 1e-7:
            break
        ux,uy = (dx/distance,dy/distance) if distance > 1e-8 else (math.cos(angle),math.sin(angle))
        correction = distance-target
        previous = {name:list(values[name]) for name in (a,b)}
        for name,weight in ((a,share),(b,-(1-share))):
            values[name][0] += weight*correction*ux
            values[name][1] += weight*correction*uy
            clamp_floor(task,values,name)
        error = abs(math.hypot(values[b][0]-values[a][0],
                               values[b][1]-values[a][1],dz)-rule['target'])
        if error < best_error:
            best_error = error
            best = {name:list(values[name]) for name in (a,b)}
        if all(abs(values[name][j]-previous[name][j]) < 1e-9
               for name in (a,b) for j in (0,1)):
            break
    for name in (a,b):
        values[name] = best[name]


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
        if METHOD == 'joint' and index%3 == 1:
            refine_relation(task,candidate,rng)
        else:
            name = rng.choice(failed or names)
            if METHOD == 'repair':
                candidate[name] = parameters(task,rng)[name]
            else:
                candidate[name] = [v+rng.gauss(0,s) for v,s in zip(candidate[name],(.25,.16,.25))]
            if name in task['zones']:
                clamp_floor(task,candidate,name)
    return {'values':candidate,'memory':{'rng':rng.getstate()}}
