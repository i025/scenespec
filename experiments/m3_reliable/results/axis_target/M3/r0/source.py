import math
import random

METHOD = 'repair'


def parameters(task, rng, yaws):
    values, groups = {}, {}
    for name, parent in task['supports'].items():
        if parent != 'floor':
            groups.setdefault(parent, []).append(name)
    for name, parent in task['supports'].items():
        if parent == 'floor':
            x0, y0, x1, y1 = task['zones'][name]
            values[name] = [rng.uniform(x0, x1), rng.uniform(y0, y1), yaws[name]]
        else:
            mount = next(q for q in task['fixed'] if q['id'] == parent)
            asset = task['spec']['assets'][mount['asset']]
            siblings = sorted(groups[parent])
            offset = (siblings.index(name) - (len(siblings) - 1) / 2) * asset['width'] * .48
            values[name] = [offset + rng.uniform(-.08, .08), rng.uniform(-.14, .02), yaws[name]]
    return values


def clamp_floor(task, values, name):
    x0, y0, x1, y1 = task['zones'][name]
    asset = next(q['asset'] for q in task['spec']['required_objects'] if q['id'] == name)
    asset = task['spec']['assets'][asset]
    x, y, yaw = values[name]
    ex = (abs(math.cos(yaw)) * asset['width'] + abs(math.sin(yaw)) * asset['depth']) / 2 + .01
    ey = (abs(math.sin(yaw)) * asset['width'] + abs(math.cos(yaw)) * asset['depth']) / 2 + .01
    values[name] = [min(x1 - ex, max(x0 + ex, x)), min(y1 - ey, max(y0 + ey, y)), yaw]


def best_index(history):
    return min(range(len(history)), key=lambda i: history[i]['report']['optimizer_loss'])


def refine_floor_relations(task, values):
    assets = {q['id']: task['spec']['assets'][q['asset']]
              for q in task['spec']['required_objects']}
    rules = [r for r in task['relations']
             if task['supports'][r['a']] == 'floor'
             and task['supports'][r['b']] == 'floor']
    if not rules:
        return

    def errors(points):
        hard, preference = 0., 0.
        for rule in rules:
            a, b = rule['a'], rule['b']
            dz = (assets[b]['height'] - assets[a]['height']) / 2
            distance = math.sqrt(sum((points[b][i] - points[a][i]) ** 2
                                     for i in (0, 1)) + dz * dz)
            violation = max(0., rule['min'] - distance, distance - rule['max'])
            hard += violation * violation
            preference += (distance - rule['target']) ** 2
        return hard, preference

    score = errors(values)
    for _ in range(16):
        gradient = {name: [0., 0.] for name in task['zones']}
        for rule in rules:
            a, b = rule['a'], rule['b']
            dx, dy = values[b][0] - values[a][0], values[b][1] - values[a][1]
            dz = (assets[b]['height'] - assets[a]['height']) / 2
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            if distance < 1e-12:
                continue
            scale = (distance - rule['target']) / distance
            for axis, delta in enumerate((dx, dy)):
                gradient[a][axis] -= scale * delta
                gradient[b][axis] += scale * delta
        largest = max(math.hypot(*g) for g in gradient.values())
        if largest < 1e-10:
            break
        step = min(.5, .25 / largest)
        for _ in range(8):
            trial = {name: point[:] for name, point in values.items()}
            for name, g in gradient.items():
                trial[name][0] -= step * g[0]
                trial[name][1] -= step * g[1]
                clamp_floor(task, trial, name)
            candidate = errors(trial)
            # Both analytic errors must improve or remain unchanged after projection.
            if candidate[0] <= score[0] and candidate[1] < score[1]:
                values.update(trial)
                score = candidate
                break
            step *= .5
        else:
            break


def layout(packet):
    history=packet['history']
    if packet['phase']=='final':return {'index':best_index(history)}
    task=packet['task'];rng=random.Random(packet['seed'])
    if packet['query']:
        raw=packet['memory']['rng'];rng.setstate((raw[0],tuple(raw[1]),raw[2]))
        yaws=packet['memory']['yaws']
    else:yaws={name:rng.uniform(-.25,.25) for name in task['supports']}
    values=parameters(task,rng,yaws)
    for rule in task['relations']:
        a,b=rule['a'],rule['b']
        if task['supports'][a]=='floor' and task['supports'][b]=='floor':
            dy=values[b][1]-values[a][1]
            dx=math.sqrt(max(0.,rule['target']**2-dy**2))
            values[b][0]=values[a][0]+(dx if values[b][0]>=values[a][0] else -dx)
    for name in task['zones']:clamp_floor(task,values,name)
    refine_floor_relations(task, values)
    return {'values':values,'memory':{'rng':rng.getstate(),'yaws':yaws}}
