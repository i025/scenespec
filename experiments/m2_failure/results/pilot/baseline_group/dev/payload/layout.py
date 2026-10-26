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
    if index:
        groups={}
        for name,parent in task['supports'].items():groups.setdefault(parent,[]).append(name)
        failed=[name for f in best['report']['failures'] for name in f['objects'] if name in values]
        choices=[parent for parent,names in groups.items() if any(name in failed for name in names)] or list(groups)
        group=rng.choice(choices);fresh=parameters(task,rng)
        for name in groups[group]:
            values[name]=fresh[name]
            if name in task['zones']:clamp_floor(task,values,name)
    return {'values':values,'memory':{'rng':rng.getstate()}}
