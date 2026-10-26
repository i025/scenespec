"""New heldout group-restart initializer; task transforms stay external."""
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


def relation_refinement(task, current):
    """Spend the last query near the incumbent without erasing its variation."""
    assets = task['spec']['assets']
    objects = {q['id']: assets[q['asset']] for q in task['spec']['required_objects']}
    fixed = {q['id']: q for q in task['fixed']}
    centres = {}
    for name, row in current.items():
        parent = task['supports'][name]
        if parent == 'floor':
            centres[name] = [row[0], row[1], objects[name]['height']/2+.003]
        else:
            mount = fixed[parent]
            roll, pitch, yaw = mount['rotation']
            cr, sr = math.cos(roll), math.sin(roll)
            cp, sp = math.cos(pitch), math.sin(pitch)
            cy, sy = math.cos(yaw), math.sin(yaw)
            u, v = row[:2]
            w = (assets[mount['asset']]['height']+objects[name]['height'])/2+.003
            # Apply Rz(yaw) Ry(pitch) Rx(roll) to the support-local centre.
            x, y, z = u, cr*v-sr*w, sr*v+cr*w
            x, z = cp*x+sp*z, -sp*x+cp*z
            centres[name] = [mount['position'][0]+cy*x-sy*y,
                             mount['position'][1]+sy*x+cy*y,
                             mount['position'][2]+z]
    for name, mount in fixed.items():
        centres[name] = list(mount['position'])
    relations = [r for r in task.get('relations', [])
                 if r['a'] in centres and r['b'] in centres]
    movable = [name for name in current if task['supports'][name] == 'floor']
    values = {name: list(row) for name, row in current.items()}

    def error(candidate):
        total = 0.
        for relation in relations:
            a, b = relation['a'], relation['b']
            pa, pb = list(centres[a]), list(centres[b])
            if a in candidate and task['supports'][a] == 'floor':
                pa[:2] = candidate[a][:2]
            if b in candidate and task['supports'][b] == 'floor':
                pb[:2] = candidate[b][:2]
            distance = math.sqrt(sum((pa[i]-pb[i])**2 for i in range(3)))
            total += abs(distance-relation['target'])
        return total

    for step in range(8):
        shifts = {name: [0., 0., 0] for name in movable}
        for relation in relations:
            a, b = relation['a'], relation['b']
            pa, pb = list(centres[a]), list(centres[b])
            if a in shifts:
                pa[:2] = values[a][:2]
            if b in shifts:
                pb[:2] = values[b][:2]
            delta = [pb[i]-pa[i] for i in range(3)]
            distance = math.sqrt(sum(d*d for d in delta))
            count = int(a in shifts)+int(b in shifts)
            if distance < 1e-9 or not count:
                continue
            amount = (distance-relation['target'])/(distance*count)
            for name, sign in ((a, 1.), (b, -1.)):
                if name in shifts:
                    shifts[name][0] += sign*amount*delta[0]
                    shifts[name][1] += sign*amount*delta[1]
                    shifts[name][2] += 1
        candidate = {name: list(row) for name, row in values.items()}
        for name, (dx, dy, count) in shifts.items():
            if not count:
                continue
            candidate[name][0] += dx/count*.5
            candidate[name][1] += dy/count*.5
            # A total displacement cap retains the seed's location and heading.
            dx = candidate[name][0]-current[name][0]
            dy = candidate[name][1]-current[name][1]
            length = math.hypot(dx, dy)
            if length > .05:
                candidate[name][0] = current[name][0]+dx*.05/length
                candidate[name][1] = current[name][1]+dy*.05/length
            clamp_floor(task, candidate, name)
        if error(candidate) >= error(values)-1e-10:
            break
        values = candidate
    return values


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
        values = relation_refinement(task, current)
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
