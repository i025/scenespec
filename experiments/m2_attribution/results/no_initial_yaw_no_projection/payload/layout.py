"""Seeded group restarts with bounded, yaw-preserving relation refinement."""
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
            values[name] = [(x0+x1)/2,(y0+y1)/2,0.*rng.uniform(-math.pi,math.pi)] if heuristic else [rng.uniform(x0,x1),rng.uniform(y0,y1),rng.uniform(-math.pi,math.pi)]
        else:
            mount = next(q for q in task['fixed'] if q['id'] == parent)
            a = task['spec']['assets'][mount['asset']]
            siblings = sorted(groups[parent])
            offset = (siblings.index(name)-(len(siblings)-1)/2)*a['width']/2
            values[name] = [offset,-.06,0.*rng.uniform(-math.pi,math.pi)] if heuristic else [rng.uniform(-a['width']*.37,a['width']*.37),rng.uniform(-a['depth']*.25,a['depth']*.25),rng.uniform(-math.pi,math.pi)]
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


def refine_relations(task, values, rng):
    """Project distance residuals onto movable support planes, keeping yaw fixed."""
    assets = task['spec']['assets']
    objects = {row['id']: assets[row['asset']]
               for row in task['spec']['required_objects']}
    fixed = {row['id']: row for row in task['fixed']}
    frames = {}
    for name, parent in task['supports'].items():
        if parent == 'floor':
            frames[name] = ([0., 0., objects[name]['height']/2+.003],
                            [1., 0., 0.], [0., 1., 0.])
            continue
        mount = fixed[parent]
        roll, pitch, yaw = mount['rotation']
        cr, sr = math.cos(roll), math.sin(roll)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cy, sy = math.cos(yaw), math.sin(yaw)
        u = [cy*cp, sy*cp, -sp]
        v = [cy*sp*sr-sy*cr, sy*sp*sr+cy*cr, cp*sr]
        normal = [cy*sp*cr+sy*sr, sy*sp*cr-cy*sr, cp*cr]
        height = (assets[mount['asset']]['height']+objects[name]['height'])/2+.003
        origin = [mount['position'][i]+height*normal[i] for i in range(3)]
        frames[name] = (origin, u, v)

    def centre(name):
        if name not in frames:
            return fixed[name]['position']
        origin, u, v = frames[name]
        x, y, yaw = values[name]
        return [origin[i]+x*u[i]+y*v[i] for i in range(3)]

    def project(name):
        if task['supports'][name] == 'floor':
            clamp_floor(task, values, name)
            return
        mount = fixed[task['supports'][name]]
        support, child = assets[mount['asset']], objects[name]
        x, y, yaw = values[name]
        c, s = abs(math.cos(yaw)), abs(math.sin(yaw))
        bx = max(0., (support['width']-c*child['width']-s*child['depth'])/2-.01)
        by = max(0., (support['depth']-s*child['width']-c*child['depth'])/2-.01)
        values[name] = [min(bx, max(-bx, x)), min(by, max(-by, y)), yaw]

    relations = [rel for rel in task['relations']
                 if (rel['a'] in frames or rel['a'] in fixed)
                 and (rel['b'] in frames or rel['b'] in fixed)
                 and (rel['a'] in frames or rel['b'] in frames)]
    for iteration in range(6):
        rng.shuffle(relations)
        for rel in relations:
            a, b = rel['a'], rel['b']
            pa, pb = centre(a), centre(b)
            delta = [pa[i]-pb[i] for i in range(3)]
            distance = math.sqrt(sum(d*d for d in delta))
            if distance < 1e-9:
                angle = rng.uniform(-math.pi, math.pi)
                direction = [math.cos(angle), math.sin(angle), 0.]
            else:
                direction = [d/distance for d in delta]
            gradients = []
            for name, sign in ((a, 1.), (b, -1.)):
                if name in frames:
                    origin, u, v = frames[name]
                    gu = sign*sum(direction[i]*u[i] for i in range(3))
                    gv = sign*sum(direction[i]*v[i] for i in range(3))
                    gradients.append((name, gu, gv))
            norm = sum(gu*gu+gv*gv for name, gu, gv in gradients)
            if norm < 1e-9:
                continue
            # Cap each move so an aspirational target cannot cause a large jump.
            target = min(rel['max'], max(rel['min'], rel['target']))
            step = max(-.25, min(.25, .7*(distance-target)/norm))
            for name, gu, gv in gradients:
                values[name][0] -= step*gu
                values[name][1] -= step*gv
                project(name)


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
    refine = index and index % 2 == 0 and any(
        rel['a'] in values or rel['b'] in values for rel in task['relations'])
    if refine:
        # Refine the preceding restart rather than repeatedly converging the best
        # layout to one preferred-distance solution across all seeds.
        values = {name:list(row) for name,row in history[-1]['values'].items()}
        refine_relations(task, {n:list(v) for n,v in values.items()}, rng)
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
