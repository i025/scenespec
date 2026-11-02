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


def floor_bounds(task, values, name):
    x0, y0, x1, y1 = task['zones'][name]
    asset = next(q['asset'] for q in task['spec']['required_objects'] if q['id'] == name)
    asset = task['spec']['assets'][asset]
    x, y, yaw = values[name]
    ex = (abs(math.cos(yaw)) * asset['width'] + abs(math.sin(yaw)) * asset['depth']) / 2 + .01
    ey = (abs(math.sin(yaw)) * asset['width'] + abs(math.cos(yaw)) * asset['depth']) / 2 + .01
    return x0 + ex, y0 + ey, x1 - ex, y1 - ey


def clamp_floor(task, values, name):
    x0, y0, x1, y1 = floor_bounds(task, values, name)
    x, y, yaw = values[name]
    values[name] = [min(x1, max(x0, x)), min(y1, max(y0, y)), yaw]


def repair_floor_relations(task, values):
    assets = {q['id']: task['spec']['assets'][q['asset']]
              for q in task['spec']['required_objects']}
    rules = [r for r in task['relations']
             if task['supports'][r['a']] == 'floor'
             and task['supports'][r['b']] == 'floor']
    bounds = {name: floor_bounds(task, values, name) for name in task['zones']}

    def error(candidate):
        total = 0.
        for rule in rules:
            a, b = rule['a'], rule['b']
            dz = (assets[b]['height'] - assets[a]['height']) / 2
            distance = math.sqrt(sum((candidate[b][i] - candidate[a][i]) ** 2
                                     for i in range(2)) + dz * dz)
            total += abs(distance - rule['target'])
        return total

    best = {name: list(value) for name, value in values.items()}
    best_error = error(best)
    for _ in range(16):
        for rule in rules:
            a, b = rule['a'], rule['b']
            dx = values[b][0] - values[a][0]
            dy = values[b][1] - values[a][1]
            distance = math.hypot(dx, dy)
            dz = (assets[b]['height'] - assets[a]['height']) / 2
            target = math.sqrt(max(0., rule['target'] ** 2 - dz * dz))
            if distance < 1e-12:
                angle = values[a][2]
                ux, uy = math.cos(angle), math.sin(angle)
            else:
                ux, uy = dx / distance, dy / distance
            for axis, direction in enumerate((ux, uy)):
                correction = (target - distance) * direction
                lo_a, hi_a = bounds[a][axis], bounds[a][axis + 2]
                lo_b, hi_b = bounds[b][axis], bounds[b][axis + 2]
                if lo_a > hi_a or lo_b > hi_b:
                    continue
                # Share displacement, then transfer any clipped portion to
                # the endpoint that still has room inside its footprint zone.
                move_a = min(hi_a - values[a][axis],
                             max(lo_a - values[a][axis], -correction / 2))
                move_b = min(hi_b - values[b][axis],
                             max(lo_b - values[b][axis], correction + move_a))
                move_a = min(hi_a - values[a][axis],
                             max(lo_a - values[a][axis], move_b - correction))
                values[a][axis] += move_a
                values[b][axis] += move_b
        current_error = error(values)
        if current_error < best_error:
            best_error = current_error
            best = {name: list(value) for name, value in values.items()}
    values.update(best)


def best_index(history):
    return min(range(len(history)), key=lambda i: history[i]['report']['optimizer_loss'])


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
    repair_floor_relations(task,values)
    return {'values':values,'memory':{'rng':rng.getstate(),'yaws':yaws}}
