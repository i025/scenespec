"""Group restarts with a bounded final preferred-distance refinement."""
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


def relation_refinement(task, values):
    """Polish translation near the incumbent without changing its rotations."""
    assets = task['spec']['assets']
    sizes = {obj['id']: assets[obj['asset']]
             for obj in task['spec']['required_objects']}
    fixed = {obj['id']: obj for obj in task['fixed']}
    centres = {name: list(obj['position']) for name, obj in fixed.items()}
    for name, row in values.items():
        parent = task['supports'][name]
        if parent == 'floor':
            centres[name] = [row[0], row[1], sizes[name]['height']/2+.003]
        else:
            mount = fixed[parent]
            roll, pitch, yaw = mount['rotation']
            cr, sr = math.cos(roll), math.sin(roll)
            cp, sp = math.cos(pitch), math.sin(pitch)
            cy, sy = math.cos(yaw), math.sin(yaw)
            u, v = row[:2]
            w = (assets[mount['asset']]['height']+sizes[name]['height'])/2+.003
            # Rz(yaw) Ry(pitch) Rx(roll), matching the support transform.
            x = cp*u+sp*(sr*v+cr*w)
            y = cr*v-sr*w
            z = -sp*u+cp*(sr*v+cr*w)
            pos = mount['position']
            centres[name] = [pos[0]+cy*x-sy*y, pos[1]+sy*x+cy*y, pos[2]+z]
    relations = [rel for rel in task.get('relations', [])
                 if rel['a'] in centres and rel['b'] in centres]
    related = {rel[key] for rel in relations for key in ('a', 'b')}
    names = [name for name in values
             if task['supports'][name] == 'floor' and name in related]
    anchors = {name: list(values[name]) for name in names}
    hard, soft = relation_errors(relations, centres)
    # Keep a small Euclidean trust region around each seeded incumbent.
    # This leaves support layouts and yaw diversity entirely to the restarts.
    for step in (.08, .04, .02, .01, .005, .0025):
        for sweep in range(3):
            changed = False
            for name in names:
                for axis in (0, 1):
                    origin = list(values[name])
                    chosen = origin
                    for direction in (-1, 1):
                        trial = list(origin)
                        trial[axis] += direction*step
                        dx = trial[0]-anchors[name][0]
                        dy = trial[1]-anchors[name][1]
                        length = math.hypot(dx, dy)
                        if length > .12:
                            trial[0] = anchors[name][0]+dx*.12/length
                            trial[1] = anchors[name][1]+dy*.12/length
                        values[name] = trial
                        clamp_floor(task, values, name)
                        centres[name][:2] = values[name][:2]
                        trial_hard, trial_soft = relation_errors(relations, centres)
                        if (trial_hard <= hard+1e-10 and
                                (trial_hard < hard-1e-10 or trial_soft < soft-1e-10)):
                            chosen = list(values[name])
                            hard, soft = trial_hard, trial_soft
                            changed = True
                    values[name] = chosen
                    centres[name][:2] = chosen[:2]
            if not changed:
                break
    return values


def relation_errors(relations, centres):
    hard, soft = 0., 0.
    for rel in relations:
        a, b = centres[rel['a']], centres[rel['b']]
        distance = math.sqrt(sum((a[i]-b[i])**2 for i in range(3)))
        hard += max(0., rel['min']-distance, distance-rel['max'])
        soft += abs(distance-rel['target'])
    return hard, soft


def layout(packet):
    history=packet['history']
    if packet['phase']=='final':
        return {'index':best_index(history)}
    task,index=packet['task'],packet['query']
    rng=random.Random(packet['seed'])
    if index:
        raw=packet['memory']['rng'];rng.setstate((raw[0],tuple(raw[1]),raw[2]))
        best=history[best_index(history)];current=best['values']
    else:
        current=parameters(task,rng,True)
        for name in task['zones']:clamp_floor(task,current,name)
    values={name:list(row) for name,row in current.items()}
    if index == 23:
        values = relation_refinement(task, values)
    elif index:
        groups={}
        for name,parent in task['supports'].items():groups.setdefault(parent,[]).append(name)
        failed=[name for f in best['report']['failures'] for name in f['objects'] if name in values]
        choices=[parent for parent,names in groups.items() if any(name in failed for name in names)] or list(groups)
        group=rng.choice(choices);fresh=parameters(task,rng)
        for name in groups[group]:
            values[name]=fresh[name]
            if name in task['zones']:clamp_floor(task,values,name)
    return {'values':values,'memory':{'rng':rng.getstate()}}
