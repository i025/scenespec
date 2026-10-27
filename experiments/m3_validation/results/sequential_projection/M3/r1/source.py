"""Independent placement with frozen yaw and bounded distance refinement."""
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
    # Refine only endpoints whose incident relations all use world coordinates.
    floor = {name for name, parent in task['supports'].items() if parent == 'floor'}
    rules = [r for r in task['relations'] if r['a'] in floor and r['b'] in floor]
    blocked = {name for r in task['relations']
               if r['a'] not in floor or r['b'] not in floor
               for name in (r['a'], r['b'])}
    heights = {q['id']: task['spec']['assets'][q['asset']]['height'] / 2
               for q in task['spec']['required_objects'] if q['id'] in floor}

    def errors():
        preference, hard = 0.0, []
        for r in rules:
            a, b = r['a'], r['b']
            distance = math.sqrt((values[b][0] - values[a][0]) ** 2
                                 + (values[b][1] - values[a][1]) ** 2
                                 + (heights[b] - heights[a]) ** 2)
            preference += abs(distance - r['target'])
            hard.append(max(0.0, r['min'] - distance, distance - r['max']))
        return preference, hard

    # Total accepted travel per endpoint is capped, including across relations.
    remaining = {name: .08 for name in floor}
    score, violations = errors()
    for rule in rules:
        a, b = rule['a'], rule['b']
        if b in blocked or b not in task['zones'] or remaining[b] <= 0:
            continue
        dx, dy = values[b][0] - values[a][0], values[b][1] - values[a][1]
        distance = math.hypot(dx, dy)
        if distance < 1e-8:
            continue
        dz = heights[b] - heights[a]
        target = math.sqrt(max(0.0, rule['target'] ** 2 - dz ** 2))
        step = max(-remaining[b], min(remaining[b], target - distance))
        old = values[b][:]
        values[b][0] += step * dx / distance
        values[b][1] += step * dy / distance
        clamp_floor(task, values, b)
        candidate, bounds = errors()
        if candidate < score and all(v <= prior for v, prior in zip(bounds, violations)):
            remaining[b] -= math.hypot(values[b][0] - old[0], values[b][1] - old[1])
            score, violations = candidate, bounds
        else:
            values[b] = old


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
    # A single ordered half projection; later clamping is never revisited.
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
