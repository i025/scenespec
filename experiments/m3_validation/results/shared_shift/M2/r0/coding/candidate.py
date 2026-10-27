"""Fixed-step search in shared displacement coordinates rather than object poses."""
import math
import random

METHOD = 'repair'


def parameters(task, rng):
    values, groups = {}, {}
    for name, parent in task['supports'].items():
        if parent != 'floor':
            groups.setdefault(parent, []).append(name)
    for name, parent in task['supports'].items():
        yaw = rng.uniform(-.25, .25)
        if parent == 'floor':
            x0, y0, x1, y1 = task['zones'][name]
            values[name] = [(x0 + x1) / 2 + rng.uniform(-.25, .25),
                            (y0 + y1) / 2 + rng.uniform(-.3, .3), yaw]
        else:
            mount = next(q for q in task['fixed'] if q['id'] == parent)
            asset = task['spec']['assets'][mount['asset']]
            siblings = sorted(groups[parent])
            offset = (siblings.index(name) - (len(siblings) - 1) / 2) * asset['width'] * .48
            values[name] = [offset + rng.uniform(-.08, .08), rng.uniform(-.14, .02), yaw]
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
    # Opposing displacements can improve distances that a shared shift cannot.
    assets = {q['id']: task['spec']['assets'][q['asset']]
              for q in task['spec']['required_objects']}
    shifts = {name: [0., 0.] for name in values
              if task['supports'][name] == 'floor'}
    for relation in task['relations']:
        a, b = relation['a'], relation['b']
        if a not in shifts or b not in shifts or a == b:
            continue
        dx = values[b][0] - values[a][0]
        dy = values[b][1] - values[a][1]
        dz = (assets[b]['height'] - assets[a]['height']) / 2
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance < 1e-9:
            continue
        target = min(relation['max'], max(relation['min'], relation['target']))
        gain = .5 * (distance - target) / distance
        shifts[a][0] += gain * dx
        shifts[a][1] += gain * dy
        shifts[b][0] -= gain * dx
        shifts[b][1] -= gain * dy
    for name, (dx, dy) in shifts.items():
        # Bound the joint correction even for objects in many relations.
        length = math.hypot(dx, dy)
        scale = min(1., .12 / length) if length else 0.
        values[name][0] += scale * dx
        values[name][1] += scale * dy


def layout(packet):
    history = packet['history']
    if packet['phase'] == 'final':
        return {'index': best_index(history)}
    task = packet['task']
    if not packet['query']:
        values = parameters(task, random.Random(packet['seed']))
    else:
        values = {name: list(v) for name, v in history[best_index(history)]['values'].items()}
        # Keep shared exploration, with relative floor motion on translation trials.
        direction = (packet['query'] - 1) % 6
        dx, dy, turn = ((.2, 0., 0.), (-.2, 0., 0.), (0., .2, 0.),
                        (0., -.2, 0.), (0., 0., .16), (0., 0., -.16))[direction]
        for name in values:
            scale = 1. if task['supports'][name] == 'floor' else .3
            values[name][0] += scale * dx
            values[name][1] += scale * dy
            values[name][2] += turn
        if direction < 4:
            refine_floor_relations(task, values)
    for name in task['zones']:
        clamp_floor(task, values, name)
    return {'values': values, 'memory': {}}
