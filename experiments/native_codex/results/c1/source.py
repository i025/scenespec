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


def refine_relations(task, values, rng, fraction):
    """Polish floor distances without changing sampled rotations or supports."""
    assets = {q['id']: task['spec']['assets'][q['asset']]
              for q in task['spec']['required_objects']}
    rules = [rule for rule in task['relations']
             if rule['a'] != rule['b']
             and task['supports'].get(rule['a']) == 'floor'
             and task['supports'].get(rule['b']) == 'floor'
             and rule['a'] in task['zones'] and rule['b'] in task['zones']]
    if not rules:
        return False
    original = {name: list(row) for name, row in values.items()}
    best = {name: list(row) for name, row in values.items()}
    initial_error = floor_relation_error(rules, assets, values)
    best_error = initial_error
    # A random mobility split leaves freedom along the distance manifold.
    share = rng.uniform(.25, .75)
    for sweep in range(12):
        for rule in rules:
            a, b = rule['a'], rule['b']
            dx = values[b][0]-values[a][0]
            dy = values[b][1]-values[a][1]
            horizontal = math.hypot(dx, dy)
            dz = (assets[b]['height']-assets[a]['height'])/2
            target = math.sqrt(max(0., rule['target']**2-dz**2))
            if horizontal < 1e-10:
                angle = rng.uniform(-math.pi, math.pi)
                ux, uy = math.cos(angle), math.sin(angle)
            else:
                ux, uy = dx/horizontal, dy/horizontal
            amount = max(-.4, min(.4, (horizontal-target)*fraction))
            # Alternate the mobility split so a saturated endpoint does not
            # prevent the other endpoint from absorbing the remaining error.
            weight = share if sweep%2 == 0 else 1-share
            for name, step in ((a, amount*weight),
                               (b, -amount*(1-weight))):
                values[name][0] += step*ux
                values[name][1] += step*uy
                clamp_floor(task, values, name)
        error = floor_relation_error(rules, assets, values)
        if error < best_error-1e-10:
            best_error = error
            best = {name: list(row) for name, row in values.items()}
    # Analytic progress only proposes a query; Mesh still judges the result.
    improved = best_error < initial_error-1e-8
    for name in values:
        values[name] = list(best[name] if improved else original[name])
    return improved


def floor_relation_error(rules, assets, values):
    error = 0.
    for rule in rules:
        a, b = rule['a'], rule['b']
        dx = values[b][0]-values[a][0]
        dy = values[b][1]-values[a][1]
        dz = (assets[b]['height']-assets[a]['height'])/2
        distance = math.sqrt(dx*dx+dy*dy+dz*dz)
        # Keep hard distance violations more expensive than soft preference.
        error += abs(distance-rule['target'])
        error += 100*(max(0., rule['min']-distance)
                      +max(0., distance-rule['max']))
    return error


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
        polished = False
        if index in (18,20,22,23) and not best_report['failures']:
            polished = refine_relations(task,candidate,rng,
                                       .5 if index in (18,22) else 1.)
        if polished:
            pass
        elif METHOD == 'joint' and index%3 == 1:
            rule = task['relations'][0]
            a,b = rule['a'],rule['b']
            dx,dy = (candidate[b][j]-candidate[a][j] for j in (0,1))
            distance = math.hypot(dx,dy)
            amount = min(.4,max(-.4,(distance-rule['target'])/3))
            for name,sign in ((a,1),(b,-1)):
                candidate[name][0] += sign*amount*dx/max(distance,1e-8)
                candidate[name][1] += sign*amount*dy/max(distance,1e-8)
                clamp_floor(task,candidate,name)
        else:
            name = rng.choice(failed or names)
            if METHOD == 'repair':
                candidate[name] = parameters(task,rng)[name]
            else:
                candidate[name] = [v+rng.gauss(0,s) for v,s in zip(candidate[name],(.25,.16,.25))]
            if name in task['zones']:
                clamp_floor(task,candidate,name)
    return {'values':candidate,'memory':{'rng':rng.getstate()}}
