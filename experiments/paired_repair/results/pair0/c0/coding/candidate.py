"""State-machine transcription of the frozen baseline; task transforms stay external."""
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


def repair_preference(task, values, rng):
    rules = [rule for rule in task['relations']
             if rule['a'] in task['zones'] and rule['b'] in task['zones']
             and rule['a'] != rule['b']]
    if not rules:
        return False
    rule = rng.choice(rules)
    a,b = rule['a'],rule['b']
    assets = {obj['id']:task['spec']['assets'][obj['asset']]
              for obj in task['spec']['required_objects']}
    dz = (assets[a]['height']-assets[b]['height'])/2
    target = math.sqrt(max(0.,rule['target']**2-dz**2))
    # Vary the pair's shared translation instead of imposing a fixed layout.
    share = rng.uniform(.2,.8)
    for _ in range(12):
        dx,dy = (values[b][j]-values[a][j] for j in (0,1))
        distance = math.hypot(dx,dy)
        error = distance-target
        if abs(error) < 1e-5:
            break
        if distance > 1e-8:
            ux,uy = dx/distance,dy/distance
        else:
            angle = rng.uniform(-math.pi,math.pi)
            ux,uy = math.cos(angle),math.sin(angle)
        # Repeated projection lets the other object move when one hits a bound.
        before = (tuple(values[a]),tuple(values[b]))
        for name,weight in ((a,share),(b,-(1-share))):
            values[name][0] += weight*error*ux
            values[name][1] += weight*error*uy
            clamp_floor(task,values,name)
        if before == (tuple(values[a]),tuple(values[b])):
            break
    return True


def best_index(history):
    best = 0
    for i,row in enumerate(history):
        if row['report']['optimizer_loss'] < history[best]['report']['optimizer_loss']-1e-8:
            best = i
    return best


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
        if not (index%3 == 1 and repair_preference(task,candidate,rng)):
            name = rng.choice(failed or names)
            if METHOD == 'repair':
                candidate[name] = parameters(task,rng)[name]
            else:
                candidate[name] = [v+rng.gauss(0,s) for v,s in zip(candidate[name],(.25,.16,.25))]
            if name in task['zones']:
                clamp_floor(task,candidate,name)
    return {'values':candidate,'memory':{'rng':rng.getstate()}}
