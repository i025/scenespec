"""Independent placement with frozen yaw and bounded relation repair."""
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


def repair_floor_relations(task, values):
    # Preserve the sampled layout: no object moves more than 8 cm and yaw
    # never changes. Mesh reports still decide which queried layout wins.
    rules = [rule for rule in task['relations']
             if task['supports'][rule['a']] == 'floor'
             and task['supports'][rule['b']] == 'floor']
    if not rules:
        return values
    assets = task['spec']['assets']
    heights = {obj['id']: assets[obj['asset']]['height'] / 2
               for obj in task['spec']['required_objects']}

    def errors(candidate):
        preference, violations = 0.0, []
        for rule in rules:
            a, b = rule['a'], rule['b']
            distance = math.sqrt(
                (candidate[b][0] - candidate[a][0]) ** 2
                + (candidate[b][1] - candidate[a][1]) ** 2
                + (heights[b] - heights[a]) ** 2)
            preference += abs(distance - rule['target'])
            violations.append(max(rule['min'] - distance,
                                  distance - rule['max'], 0.0))
        return preference, violations

    shifts = {name: [0.0, 0.0] for name in values}
    for rule in rules:
        a, b = rule['a'], rule['b']
        dx, dy = values[b][0] - values[a][0], values[b][1] - values[a][1]
        distance = math.sqrt(dx * dx + dy * dy
                             + (heights[b] - heights[a]) ** 2)
        if distance <= 1e-8:
            continue
        amount = .5 * (distance - rule['target']) / distance
        for axis, delta in enumerate((dx, dy)):
            shifts[a][axis] += amount * delta
            shifts[b][axis] -= amount * delta
    for shift in shifts.values():
        length = math.hypot(*shift)
        if length > .08:
            shift[0] *= .08 / length
            shift[1] *= .08 / length
    before, hard_before = errors(values)
    for scale in (1.0, .5, .25):
        candidate = {name: list(value) for name, value in values.items()}
        for name in task['zones']:
            candidate[name][0] += scale * shifts[name][0]
            candidate[name][1] += scale * shifts[name][1]
            clamp_floor(task, candidate, name)
        after, hard_after = errors(candidate)
        if (after < before - 1e-10
                and all(new <= old for new, old in zip(hard_after, hard_before))
                and all(math.hypot(candidate[name][0] - values[name][0],
                                   candidate[name][1] - values[name][1]) <= .0800000001
                        for name in values)):
            return candidate
    return values


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
    # Retain the original proposal before a bounded, joint floor repair.
    for rule in task['relations']:
        a, b = rule['a'], rule['b']
        dx, dy = values[b][0] - values[a][0], values[b][1] - values[a][1]
        distance = math.hypot(dx, dy)
        amount = .5 * (rule['target'] - distance) / max(distance, 1e-8)
        values[b][0] += amount * dx
        values[b][1] += amount * dy
        if b in task['zones']:
            clamp_floor(task, values, b)
    values = repair_floor_relations(task, values)
    return {'values': values, 'memory': {'rng': rng.getstate(), 'yaws': yaws}}
