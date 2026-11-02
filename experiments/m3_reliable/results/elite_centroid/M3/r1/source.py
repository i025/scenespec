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


def floor_preference_error(relations, values, heights):
    return sum(abs(math.sqrt(
        (values[r['a']][0] - values[r['b']][0]) ** 2
        + (values[r['a']][1] - values[r['b']][1]) ** 2
        + (heights[r['a']] - heights[r['b']]) ** 2
    ) - r['target']) for r in relations)


def polish_floor_relations(task, values):
    floor = {name for name, parent in task['supports'].items() if parent == 'floor'}
    relations = [r for r in task['relations'] if r['a'] in floor and r['b'] in floor]
    # Leave endpoints with other kinds of relations alone: their world-space
    # preference errors are not included in the floor-only acceptance check.
    blocked = {name for r in task['relations']
               if r['a'] not in floor or r['b'] not in floor
               for name in (r['a'], r['b'])}
    assets = {q['id']: task['spec']['assets'][q['asset']]
              for q in task['spec']['required_objects'] if q['id'] in floor}
    heights = {name: asset['height'] / 2 + .003 for name, asset in assets.items()}
    bounds = {}
    for name in floor:
        x0, y0, x1, y1 = task['zones'][name]
        yaw = values[name][2]
        asset = assets[name]
        ex = (abs(math.cos(yaw)) * asset['width'] + abs(math.sin(yaw)) * asset['depth']) / 2 + .01
        ey = (abs(math.sin(yaw)) * asset['width'] + abs(math.cos(yaw)) * asset['depth']) / 2 + .01
        bounds[name] = (x0 + ex, y0 + ey, x1 - ex, y1 - ey)
    original = {name: value[:] for name, value in values.items()}
    remaining = {name: .06 for name in floor}
    before = floor_preference_error(relations, values, heights)
    for r in relations:
        a, b = r['a'], r['b']
        if a == b or a in blocked or b in blocked:
            continue
        dx, dy = values[b][0] - values[a][0], values[b][1] - values[a][1]
        distance = math.hypot(dx, dy)
        if distance < 1e-12:
            continue
        target = math.sqrt(max(0., r['target'] ** 2 - (heights[a] - heights[b]) ** 2))
        limit = min(remaining[a], remaining[b])
        step = max(-limit, min(limit, (distance - target) / 2))
        shift = (dx / distance * step, dy / distance * step)
        fraction = 1.
        # Equal and opposite moves preserve the pair midpoint. Clip their
        # common fraction so neither rotated footprint can leave its zone.
        for name, sign in ((a, 1.), (b, -1.)):
            for axis in (0, 1):
                movement = sign * shift[axis]
                if movement > 0:
                    fraction = min(fraction, max(0., (bounds[name][axis + 2] - values[name][axis]) / movement))
                elif movement < 0:
                    fraction = min(fraction, max(0., (bounds[name][axis] - values[name][axis]) / movement))
        for axis in (0, 1):
            values[a][axis] += fraction * shift[axis]
            values[b][axis] -= fraction * shift[axis]
        spent = abs(step) * fraction
        remaining[a] = max(0., remaining[a] - spent)
        remaining[b] = max(0., remaining[b] - spent)
    # Shared endpoints can trade off residuals. Keep only an aggregate gain;
    # total movement per object is bounded and yaw is never changed.
    if floor_preference_error(relations, values, heights) >= before:
        values.update(original)


def layout(packet):
    history=packet['history']
    if packet['phase']=='final':return {'index':best_index(history)}
    task=packet['task'];rng=random.Random(packet['seed'])
    if packet['query']:
        raw=packet['memory']['rng'];rng.setstate((raw[0],tuple(raw[1]),raw[2]))
        yaws=packet['memory']['yaws']
    else:yaws={name:rng.uniform(-.25,.25) for name in task['supports']}
    if packet['query']<8:
        values=parameters(task,rng,yaws)
    else:
        elite=sorted(history,key=lambda row:row['report']['optimizer_loss'])[:3]
        values={name:[sum(row['values'][name][axis] for row in elite)/len(elite) for axis in range(3)] for name in task['supports']}
        for name in values:
            scale=(.18 if task['supports'][name]=='floor' else .025)*(1.-packet['query']/32.)
            values[name][0]+=rng.uniform(-scale,scale)
            values[name][1]+=rng.uniform(-scale,scale)
    for name in task['zones']:clamp_floor(task,values,name)
    if packet['query'] >= 16:
        polish_floor_relations(task, values)
    return {'values':values,'memory':{'rng':rng.getstate(),'yaws':yaws}}
