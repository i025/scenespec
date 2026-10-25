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


def best_index(history):
    best = 0
    for i,row in enumerate(history):
        if row['report']['optimizer_loss'] < history[best]['report']['optimizer_loss']-1e-8:
            best = i
    return best


def refine_relation(task, values, rng):
    rules = [rule for rule in task['relations']
             if rule['a'] in values and rule['b'] in values
             and rule['a'] != rule['b']
             and task['supports'][rule['a']] == 'floor'
             and task['supports'][rule['b']] == 'floor']
    if not rules:
        return False
    rule = rng.choice(rules)
    a,b = rule['a'],rule['b']
    assets = {obj['id']:task['spec']['assets'][obj['asset']]
              for obj in task['spec']['required_objects']}
    dz = (assets[a]['height']-assets[b]['height'])/2
    target = math.sqrt(max(0.,rule['target']**2-dz**2))
    # Reproject after clamping so the free endpoint can absorb a blocked move.
    for _ in range(3):
        dx,dy = (values[b][j]-values[a][j] for j in (0,1))
        distance = math.hypot(dx,dy)
        if distance < 1e-8:
            angle = rng.uniform(-math.pi,math.pi)
            ux,uy = math.cos(angle),math.sin(angle)
        else:
            ux,uy = dx/distance,dy/distance
        amount = min(.4,max(-.4,(distance-target)/2))
        if abs(amount) < 1e-6:
            break
        for name,sign in ((a,1),(b,-1)):
            values[name][0] += sign*amount*ux
            values[name][1] += sign*amount*uy
            clamp_floor(task,values,name)
    return True


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
        refined = index%3 == 1 and refine_relation(task,candidate,rng)
        if not refined:
            name = rng.choice(failed or names)
            if METHOD == 'repair':
                candidate[name] = parameters(task,rng)[name]
            else:
                candidate[name] = [v+rng.gauss(0,s) for v,s in zip(candidate[name],(.25,.16,.25))]
            if name in task['zones']:
                clamp_floor(task,candidate,name)
    return {'values':candidate,'memory':{'rng':rng.getstate()}}
