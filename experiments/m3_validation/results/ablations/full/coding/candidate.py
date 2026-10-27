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
    rules = [rule for rule in task['relations']
             if task['supports'][rule['a']] == 'floor'
             and task['supports'][rule['b']] == 'floor'
             and rule['a'] != rule['b']]
    if not rules:
        return
    heights = {obj['id']: task['spec']['assets'][obj['asset']]['height'] / 2 + .003
               for obj in task['spec']['required_objects']
               if obj['id'] in task['zones']}
    anchors = {name: point[:] for name, point in values.items()}

    def residuals():
        errors, violations = [], []
        for rule in rules:
            a, b = rule['a'], rule['b']
            distance = math.sqrt((values[b][0] - values[a][0]) ** 2
                                 + (values[b][1] - values[a][1]) ** 2
                                 + (heights[b] - heights[a]) ** 2)
            errors.append(abs(distance - rule['target']))
            violations.append(max(rule['min'] - distance, 0.0,
                                  distance - rule['max']))
        return sum(errors), violations

    # Every accepted step reduces aggregate preference error without increasing
    # any floor-pair hard residual. Stay within 8 cm of the original proposal;
    # keep its yaw, support offsets and random stream exactly intact.
    score, violations = residuals()
    for _ in range(4):
        changed = False
        for rule in rules:
            a, b = rule['a'], rule['b']
            dx = values[b][0] - values[a][0]
            dy = values[b][1] - values[a][1]
            planar = math.hypot(dx, dy)
            if planar < 1e-8:
                continue
            dz = heights[b] - heights[a]
            target = math.sqrt(max(0.0, rule['target'] ** 2 - dz ** 2))
            amount = .5 * (target - planar) / planar
            before_a, before_b = values[a][:], values[b][:]
            for scale in (1.0, .5, .25):
                for name, before, sign in ((a, before_a, -1), (b, before_b, 1)):
                    x = before[0] + sign * scale * amount * dx
                    y = before[1] + sign * scale * amount * dy
                    ax, ay = anchors[name][:2]
                    length = math.hypot(x - ax, y - ay)
                    fraction = min(1.0, .08 / max(length, 1e-8))
                    values[name] = [ax + fraction * (x - ax),
                                    ay + fraction * (y - ay), before[2]]
                    clamp_floor(task, values, name)
                candidate, hard = residuals()
                if (candidate < score - 1e-10
                        and all(new <= old + 1e-12
                                for new, old in zip(hard, violations))):
                    score, violations = candidate, hard
                    changed = True
                    break
                values[a], values[b] = before_a[:], before_b[:]
        if not changed:
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
    # Preserve the original random proposal and initial ordered projection.
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
