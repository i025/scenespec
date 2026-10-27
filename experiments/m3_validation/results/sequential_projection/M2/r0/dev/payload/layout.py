"""Independent placement with frozen yaw and bounded distance repair."""
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


def world_center(task, values, name, assets):
    u, v, _ = values[name]
    parent = task['supports'][name]
    if parent == 'floor':
        return u, v, assets[name]['height'] / 2 + .003
    mount = next(q for q in task['fixed'] if q['id'] == parent)
    height = task['spec']['assets'][mount['asset']]['height']
    w = (height + assets[name]['height']) / 2 + .003
    roll, pitch, yaw = mount['rotation']
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    # Apply the support's Rz(yaw) Ry(pitch) Rx(roll) transform.
    ry, rz = cr * v - sr * w, sr * v + cr * w
    rx, rz = cp * u + sp * rz, -sp * u + cp * rz
    x, y, z = mount['position']
    return x + cy * rx - sy * ry, y + sy * rx + cy * ry, z + rz


def repair_relations(task, values):
    assets = {q['id']: task['spec']['assets'][q['asset']]
              for q in task['spec']['required_objects']}
    for rule in task['relations']:
        a, b = rule['a'], rule['b']
        if task['supports'][b] != 'floor':
            a, b = b, a
        if task['supports'][b] != 'floor':
            continue
        ax, ay, az = world_center(task, values, a, assets)
        bx, by, bz = world_center(task, values, b, assets)
        yaw = values[b][2]
        asset = assets[b]
        ex = (abs(math.cos(yaw)) * asset['width'] + abs(math.sin(yaw)) * asset['depth']) / 2 + .01
        ey = (abs(math.sin(yaw)) * asset['width'] + abs(math.cos(yaw)) * asset['depth']) / 2 + .01
        x0, y0, x1, y1 = task['zones'][b]
        x0, y0, x1, y1 = x0 + ex, y0 + ey, x1 - ex, y1 - ey
        if x0 > x1 or y0 > y1:
            continue
        dz = bz - az
        radius = math.sqrt(max(0., rule['target'] ** 2 - dz ** 2))
        dx, dy = bx - ax, by - ay
        distance = math.hypot(dx, dy)
        candidates = [(bx, by), (min(x1, max(x0, ax)), min(y1, max(y0, ay)))]
        candidates.extend((x, y) for x in (x0, x1) for y in (y0, y1))
        if distance > 1e-10:
            candidates.append((min(x1, max(x0, ax + radius * dx / distance)),
                               min(y1, max(y0, ay + radius * dy / distance))))
        else:
            candidates.extend(((ax + radius, ay), (ax - radius, ay),
                               (ax, ay + radius), (ax, ay - radius)))
        # Circle/edge intersections cover feasible targets when radial motion
        # hits a boundary. Corners and the nearest point cover unreachable ones.
        for x in (x0, x1):
            remainder = radius ** 2 - (x - ax) ** 2
            if remainder >= 0:
                root = math.sqrt(remainder)
                candidates.extend(((x, ay - root), (x, ay + root)))
        for y in (y0, y1):
            remainder = radius ** 2 - (y - ay) ** 2
            if remainder >= 0:
                root = math.sqrt(remainder)
                candidates.extend(((ax - root, y), (ax + root, y)))
        best_error, best_move = float('inf'), float('inf')
        for x, y in candidates:
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            error = abs(math.sqrt((x - ax) ** 2 + (y - ay) ** 2 + dz ** 2) - rule['target'])
            move = (x - bx) ** 2 + (y - by) ** 2
            if error < best_error - 1e-9 or (abs(error - best_error) <= 1e-9 and move < best_move):
                best_error, best_move = error, move
                values[b] = [x, y, yaw]


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
    repair_relations(task, values)
    return {'values': values, 'memory': {'rng': rng.getstate(), 'yaws': yaws}}
