"""Independent continuous placement with frozen yaw and bounded relation repair."""
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


def layout(packet):
    if packet['phase'] == 'final':
        return {'index': best_index(packet['history'])}
    task = packet['task']
    rng = random.Random(packet['seed'])
    if packet['query']:
        raw = packet['memory']['rng']
        rng.setstate((raw[0], tuple(raw[1]), raw[2]))
        yaws = packet['memory']['yaws']
    else:
        yaws = {name: rng.uniform(-.25, .25) for name in task['supports']}
    values = parameters(task, rng, yaws)
    for name in task['zones']:
        clamp_floor(task, values, name)
    # Preserve the sampled anchor and yaw while repairing the other endpoint.
    assets = {q['id']: task['spec']['assets'][q['asset']]
              for q in task['spec']['required_objects']}
    for rule in task['relations']:
        a, b = rule['a'], rule['b']
        floor_pair = task['supports'][a] == task['supports'][b] == 'floor'
        target = rule['target']
        if floor_pair:
            # Floor centre heights are fixed; relations use 3D distance.
            dz = (assets[b]['height'] - assets[a]['height']) / 2
            target = math.sqrt(max(0.0, target * target - dz * dz))
        for _ in range(4 if floor_pair else 1):
            dx, dy = values[b][0] - values[a][0], values[b][1] - values[a][1]
            distance = math.hypot(dx, dy)
            if distance < 1e-8:
                if target < 1e-8:
                    break
                angle = rng.uniform(-math.pi, math.pi)
                ux, uy = math.cos(angle), math.sin(angle)
            else:
                ux, uy = dx / distance, dy / distance
            old_x, old_y = values[b][:2]
            step = .5 * (target - distance)
            values[b][0] += step * ux
            values[b][1] += step * uy
            if b in task['zones']:
                clamp_floor(task, values, b)
            if math.hypot(values[b][0] - old_x, values[b][1] - old_y) < 1e-8:
                break
    return {'values': values, 'memory': {'rng': rng.getstate(), 'yaws': yaws}}
