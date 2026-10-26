"""Random repair with bounded joint proposals for preferred centre distances."""
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


def project_relations(task, values, rng):
    """Explore floor positions, then project distances inside footprint bounds.

    Mesh feedback still decides whether the resulting proposal is useful.
    Keep rotations and supported objects intact to preserve their contacts.
    """
    assets = task['spec']['assets']
    objects = {q['id']:assets[q['asset']]
               for q in task['spec']['required_objects']}
    rules = [rule for rule in task['relations']
             if all(name in values and name in task['zones']
                    and task['supports'][name] == 'floor'
                    for name in (rule['a'],rule['b']))]
    names = sorted({name for rule in rules
                    for name in (rule['a'],rule['b'])})
    if not rules:
        return False
    # Explore along the feasible distance manifold instead of repeatedly
    # returning the same nearest solution from the incumbent.
    for name in names:
        values[name][0] += rng.gauss(0,.22)
        values[name][1] += rng.gauss(0,.22)
        clamp_floor(task,values,name)
    mobility = {name:rng.uniform(.25,1.) for name in names}
    for _ in range(48):
        largest = 0.
        for rule in rules:
            a,b = rule['a'],rule['b']
            dx = values[b][0]-values[a][0]
            dy = values[b][1]-values[a][1]
            distance = math.hypot(dx,dy)
            dz = (objects[b]['height']-objects[a]['height'])/2
            target = math.sqrt(max(0.,rule['target']**2-dz**2))
            error = distance-target
            largest = max(largest,abs(error))
            if distance < 1e-10:
                angle = rng.uniform(-math.pi,math.pi)
                ux,uy = math.cos(angle),math.sin(angle)
            else:
                ux,uy = dx/distance,dy/distance
            # Clamping may immobilize one endpoint; repeated updates let
            # the other endpoint take up the remaining correction.
            total = mobility[a]+mobility[b]
            for name,sign in ((a,1),(b,-1)):
                step = sign*.8*error*mobility[name]/total
                values[name][0] += step*ux
                values[name][1] += step*uy
                clamp_floor(task,values,name)
        if largest < 1e-5:
            break
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
        if not (index%3 == 1 and project_relations(task,candidate,rng)):
            name = rng.choice(failed or names)
            candidate[name] = parameters(task,rng)[name]
            if name in task['zones']:
                clamp_floor(task,candidate,name)
    return {'values':candidate,'memory':{'rng':rng.getstate()}}
