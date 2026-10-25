"""Three fixed, trusted continuous baselines sharing the same query budget."""
import copy
import math
import random
import time

from .geometry import pose
from .tasks import supported

METHODS = ('random','repair','joint')


def parameters(task, rng, heuristic=False):
    values = {}
    groups = {}
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


def state(task, values):
    objects = copy.deepcopy(task['fixed'])
    required = {q['id']:q['asset'] for q in task['spec']['required_objects']}
    for name,(u,v,yaw) in values.items():
        if task['supports'][name] == 'floor':
            a = task['spec']['assets'][required[name]]
            objects.append(pose(name,required[name],[u,v,a['height']/2+.003],[0,0,yaw]))
        else:
            objects.append(supported(task,name,u,v,yaw))
    return {'scene_id':task['spec']['scene_id'],'objects':objects}


def clamp_floor(task, values, name):
    x0,y0,x1,y1 = task['zones'][name]
    a = next(q['asset'] for q in task['spec']['required_objects'] if q['id'] == name)
    a = task['spec']['assets'][a]
    x,y,yaw = values[name]
    ex = (abs(math.cos(yaw))*a['width']+abs(math.sin(yaw))*a['depth'])/2+.01
    ey = (abs(math.sin(yaw))*a['width']+abs(math.cos(yaw))*a['depth'])/2+.01
    values[name] = [min(x1-ex,max(x0+ex,x)),min(y1-ey,max(y0+ey,y)),yaw]


def generate(task, world, method, seed, budget=24, cpu_seconds=5.):
    rng = random.Random(seed)
    started,wall = time.process_time(),time.perf_counter()
    current = parameters(task,rng,heuristic=method != 'random')
    for name in task['zones']:
        if method != 'random':
            clamp_floor(task,current,name)
    best,best_loss,best_report = None,float('inf'),None
    trace = []
    for index in range(budget):
        if time.process_time()-started > cpu_seconds:
            break
        if index == 0:
            candidate = copy.deepcopy(current)
        elif method == 'random':
            candidate = parameters(task,rng)
        else:
            candidate = copy.deepcopy(current)
            names = list(candidate)
            failed = [name for f in best_report['failures'] for name in f['objects'] if name in candidate]
            if method == 'joint' and index%3 == 1:
                # Coupled continuous descent on the preferred inter-object distance.
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
                if method == 'repair':
                    candidate[name] = parameters(task,rng)[name]
                else:
                    candidate[name] = [v+rng.gauss(0,s) for v,s in zip(candidate[name],(.25,.16,.25))]
                if name in task['zones']:
                    clamp_floor(task,candidate,name)
        output = state(task,candidate)
        report = world.evaluate(output)
        trace.append({'query':index,'optimizer_loss':report['optimizer_loss'],
                      'failures':[f['type'] for f in report['failures']], 'quality':report['quality']})
        if report['optimizer_loss'] < best_loss-1e-8:
            best,best_loss,best_report = output,report['optimizer_loss'],report
            current = candidate
    return best,{'queries':len(trace),'query_budget':budget,'cpu_seconds':time.process_time()-started,
                 'wall_seconds':time.perf_counter()-wall,'trace':trace,
                 'policy':'24-query ceiling; 5 CPU seconds checked before next query; query cost included; final physics/navigation excluded'}
