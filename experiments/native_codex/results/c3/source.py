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


def project_relation(task, values, rule, rng):
    """Fit a floor relation inside yaw-dependent centre bounds, without Mesh calls."""
    a,b = rule['a'],rule['b']
    assets = {q['id']:task['spec']['assets'][q['asset']]
              for q in task['spec']['required_objects']}
    dz = (assets[a]['height']-assets[b]['height'])/2
    target = min(rule['max'],max(rule['min'],rule['target']))
    planar_target = math.sqrt(max(0.,target*target-dz*dz))
    original = {name:list(values[name]) for name in (a,b)}
    # Rectangular footprints repeat every pi. Prefer the nearest equivalent yaw
    # so that achieving a distance does not require gratuitous rotation.
    angles = {}
    for name in (a,b):
        yaw = original[name][2]
        angles[name] = [yaw] + [yaw+(axis-yaw+math.pi/2)%math.pi-math.pi/2
                                for axis in (0.,math.pi/2)]
    choices = [(ya,yb) for ya in angles[a] for yb in angles[b]]
    choices += [(original[a][2]+rng.uniform(-math.pi/2,math.pi/2),
                 original[b][2]+rng.uniform(-math.pi/2,math.pi/2))
                for _ in range(3)]
    original_distance = math.sqrt((original[b][0]-original[a][0])**2+
                                  (original[b][1]-original[a][1])**2+dz*dz)
    best = original
    best_score = (round(abs(original_distance-target),7),0.)
    for ya,yb in choices:
        trial = {a:[original[a][0],original[a][1],ya],
                 b:[original[b][0],original[b][1],yb]}
        feasible = True
        for name in (a,b):
            asset = assets[name]
            x0,y0,x1,y1 = task['zones'][name]
            yaw = trial[name][2]
            width = abs(math.cos(yaw))*asset['width']+abs(math.sin(yaw))*asset['depth']+.02
            depth = abs(math.sin(yaw))*asset['width']+abs(math.cos(yaw))*asset['depth']+.02
            if width > x1-x0 or depth > y1-y0:
                feasible = False
                break
            clamp_floor(task,trial,name)
        if not feasible:
            continue
        # Alternate endpoints: when one hits its bounds the other can absorb
        # the remaining correction. Each move is projected into its own zone.
        for _ in range(12):
            for name,sign,fraction in ((a,1,.5),(b,-1,1.),(a,1,1.)):
                dx = trial[b][0]-trial[a][0]
                dy = trial[b][1]-trial[a][1]
                distance = math.hypot(dx,dy)
                if distance < 1e-10:
                    angle = rng.uniform(-math.pi,math.pi)
                    ux,uy = math.cos(angle),math.sin(angle)
                else:
                    ux,uy = dx/distance,dy/distance
                amount = fraction*(distance-planar_target)
                trial[name][0] += sign*amount*ux
                trial[name][1] += sign*amount*uy
                clamp_floor(task,trial,name)
        distance = math.sqrt((trial[b][0]-trial[a][0])**2+
                             (trial[b][1]-trial[a][1])**2+dz*dz)
        movement = sum((trial[name][j]-original[name][j])**2
                       for name in (a,b) for j in (0,1,2))
        score = (round(abs(distance-target),7),movement)
        if score < best_score:
            best,best_score = trial,score
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
        floor_relations = [rule for rule in task['relations']
                           if rule['a'] != rule['b'] and
                           task['supports'].get(rule['a']) == 'floor' and
                           task['supports'].get(rule['b']) == 'floor' and
                           rule['a'] in task['zones'] and rule['b'] in task['zones']]
        if METHOD == 'joint' and index%3 == 1 and floor_relations:
            project_relation(task,candidate,rng.choice(floor_relations),rng)
        else:
            name = rng.choice(failed or names)
            if METHOD == 'repair':
                candidate[name] = parameters(task,rng)[name]
            else:
                candidate[name] = [v+rng.gauss(0,s) for v,s in zip(candidate[name],(.25,.16,.25))]
            if name in task['zones']:
                clamp_floor(task,candidate,name)
    return {'values':candidate,'memory':{'rng':rng.getstate()}}
