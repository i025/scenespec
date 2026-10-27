"""Independent placement with frozen yaw and bounded relation refinement."""
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
    rules = [rule for rule in task['relations']
             if task['supports'][rule['a']] == 'floor'
             and task['supports'][rule['b']] == 'floor']
    anchors = {name: values[name][:2] for name in task['zones']}

    def error():
        total = 0.0
        for rule in rules:
            a, b = rule['a'], rule['b']
            dz = (assets[b]['height'] - assets[a]['height']) / 2
            distance = math.sqrt((values[b][0] - values[a][0]) ** 2
                                 + (values[b][1] - values[a][1]) ** 2 + dz * dz)
            total += abs(distance - rule['target'])
        return total

    # Refine the original proposal, preserving yaw and a 15cm translation radius.
    score = error()
    for _ in range(4):
        improved = False
        for rule in rules:
            a, b = rule['a'], rule['b']
            if b not in anchors:
                continue
            dx = values[b][0] - values[a][0]
            dy = values[b][1] - values[a][1]
            distance = math.hypot(dx, dy)
            if distance < 1e-8:
                continue
            dz = (assets[b]['height'] - assets[a]['height']) / 2
            target = math.sqrt(max(0.0, rule['target'] ** 2 - dz * dz))
            amount = (target - distance) / distance
            previous = values[b][:]
            for fraction in (1.0, .5, .25):
                x = previous[0] + fraction * amount * dx
                y = previous[1] + fraction * amount * dy
                ax, ay = anchors[b]
                offset = math.hypot(x - ax, y - ay)
                scale = min(1.0, .15 / max(offset, 1e-8))
                values[b] = [ax + scale * (x - ax), ay + scale * (y - ay), previous[2]]
                clamp_floor(task, values, b)
                candidate_score = error()
                if candidate_score < score - 1e-10:
                    score = candidate_score
                    improved = True
                    break
                values[b] = previous[:]
        if not improved:
            break


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
    # Keep the original randomized proposal as the refinement anchor.
    for rule in task['relations']:
        a, b = rule['a'], rule['b']
        dx, dy = values[b][0] - values[a][0], values[b][1] - values[a][1]
        distance = math.hypot(dx, dy)
        amount = .5 * (rule['target'] - distance) / max(distance, 1e-8)
        values[b][0] += amount * dx
        values[b][1] += amount * dy
        if b in task['zones']:
            clamp_floor(task, values, b)
    refine_floor_relations(task, values)
    return {'values': values, 'memory': {'rng': rng.getstate(), 'yaws': yaws}}
