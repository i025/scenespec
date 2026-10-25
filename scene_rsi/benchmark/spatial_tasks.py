"""Small 3D layout tasks. Old SceneState stays planar; elevations are separate data.

The original verifier checks the ground projection. This additional trusted checker
checks ALL elevated geometry, support, room membership, clearance and camera rays.
Neither checker nor the private feasible witnesses are mounted into candidates.
"""
from collections import Counter
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import random
import statistics
import time

import pybullet as bullet

from ..agent.fixed_agent import FixedSceneAgent
from ..evaluator.verifier import SceneVerifier
from ..geometry import footprint
from ..meta.layout_process import LayoutProcess
from ..schema import Asset, PlacedObject, RequiredObject, Room, SceneSpec, SceneState
from ..world.algorithm_experiment import audit
from ..world.generator_benchmark import save
from ..world.planning import walk

FAMILIES = ('support', 'rooms', 'visibility')
SEEDS = {'calibration':2100000, 'dev':2200000, 'selection':2300000, 'test':2400000}


def bounds(spec, pose):
    obj = PlacedObject(**{k:pose[k] for k in ('id', 'asset', 'x', 'y', 'yaw_deg')})
    x0, y0, x1, y1 = footprint(obj, spec.assets[obj.asset])
    return (x0, y0, pose['z'], x1, y1, pose['z']+spec.assets[obj.asset].height)


def overlap(a, b):
    return all(min(a[i+3], b[i+3])-max(a[i], b[i]) > 1e-7 for i in range(3))


def ray_box(origin, target, box):
    """Segment/AABB intersection, excluding the target endpoint."""
    lo, hi = 0., 1.-1e-6
    for i in range(3):
        delta = target[i]-origin[i]
        if abs(delta) < 1e-10:
            if not box[i] <= origin[i] <= box[i+3]:
                return False
        else:
            a, b = (box[i]-origin[i])/delta, (box[i+3]-origin[i])/delta
            lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
            if lo > hi:
                return False
    return True


def visible_fraction(view, name, boxes):
    """27 interior sample rays, pinhole frustum, conservative solid asset envelopes.

    This is geometric proxy visibility, not photorealistic segmentation accuracy.
    Room boundaries are open cutaways; only task objects occlude, including dividers.
    """
    origin = view['position']
    forward = [b-a for a, b in zip(origin, view['target'])]
    norm = math.sqrt(sum(v*v for v in forward))
    forward = [v/norm for v in forward]
    norm = math.hypot(forward[0], forward[1])
    right = [forward[1]/norm, -forward[0]/norm, 0.]
    up = [right[1]*forward[2], -right[0]*forward[2], right[0]*forward[1]-right[1]*forward[0]]
    tangent = math.tan(math.radians(view['fov']/2))
    box, visible = boxes[name], 0
    for fx in (.15, .5, .85):
        for fy in (.15, .5, .85):
            for fz in (.15, .5, .85):
                point = [box[i]+f*(box[i+3]-box[i]) for i, f in enumerate((fx, fy, fz))]
                delta = [b-a for a, b in zip(origin, point)]
                depth = sum(a*b for a, b in zip(delta, forward))
                framed = depth > 0 and all(abs(sum(a*b for a, b in zip(delta, axis))) <= depth*tangent
                                           for axis in (right, up))
                if framed and not any(ray_box(origin, point, other) for key, other in boxes.items() if key != name):
                    visible += 1
    return visible/27


def navigation(packet, boxes):
    """Existing walk BFS; .5m grid, .2m radius, 1.8m headroom.

    Finer than the minimum inflated wall width (.7m), so edges cannot jump
    through the task's thin dividers. Input endpoints retain metre-cell meaning.
    """
    free = {(x, y) for x in range(2*packet['width']-1) for y in range(2*packet['depth']-1)
            if not any(b[2] < 1.8-1e-7 and b[5] > 1e-7
                       and b[0]-.2 < x*.5+.5 < b[3]+.2 and b[1]-.2 < y*.5+.5 < b[4]+.2
                       for b in boxes.values())}
    class Grid:
        def free(self, cell):
            return cell in free
    start, goal = tuple(2*v for v in packet['start']), tuple(2*v for v in packet['goal'])
    return walk(Grid(), start, goal, (-100, -100)) if start in free and goal in free else None


def physics(spec, poses):
    """Independent PyBullet envelope contacts, no candidate execution in this process."""
    client = bullet.connect(bullet.DIRECT)
    try:
        bodies = []
        for p in poses.values():
            a = spec.assets[p['asset']]
            shape = bullet.createCollisionShape(bullet.GEOM_BOX, halfExtents=[a.width/2, a.depth/2, a.height/2],
                                                physicsClientId=client)
            body = bullet.createMultiBody(baseMass=0, baseCollisionShapeIndex=shape,
                       basePosition=[p['x'], p['y'], p['z']+a.height/2],
                       baseOrientation=bullet.getQuaternionFromEuler([0, 0, math.radians(p['yaw_deg'])]),
                       physicsClientId=client)
            bodies.append(body)
        return not any(any(point[8] < -1e-6 for point in bullet.getClosestPoints(a, b, 0, physicsClientId=client))
                       for i, a in enumerate(bodies) for b in bodies[i+1:])
    finally:
        bullet.disconnect(client)


def unpack(response):
    state = SceneState.from_dict(response['state'])
    elevations = response['elevations']
    if set(elevations) != {o.id for o in state.objects}:
        raise ValueError('one elevation per scene object required')
    poses = {o.id:dict(asdict(o), z=elevations[o.id]) for o in state.objects}
    if len(poses) != len(state.objects):
        raise ValueError('duplicate object')
    if any(type(p['z']) not in (int, float) or not math.isfinite(p['z']) for p in poses.values()):
        raise ValueError('finite elevations required')
    return state, poses


def check(case, response):
    packet = case['packet']
    spec = SceneSpec.from_dict(packet['spec'])
    state, poses = unpack(response)
    required = spec.required_lookup()
    malformed = ([{'type':'missing_object', 'objects':[k]} for k in required if k not in poses]
                 +[{'type':'unexpected_object', 'objects':[k]} for k in poses if k not in required]
                 +[{'type':'wrong_asset', 'objects':[k]} for k, p in poses.items()
                   if k in required and p['asset'] != required[k].asset])
    if malformed:
        return {'valid':False, 'success':False, 'failures':malformed, 'shortest_actions':None,
                'turns':0, 'visibility':{}, 'physical_valid':None, 'scene':response}
    if state.scene_id != spec.scene_id or any(p['yaw_deg'] not in (0, 90, 180, 270)
            or not all(math.isfinite(p[k]) for k in ('x', 'y')) for p in poses.values()):
        raise ValueError('invalid scene transform')
    ground = tuple(o for o in state.objects if abs(poses[o.id]['z']) < 1e-7)
    ground_spec = replace(spec, required_objects=tuple(RequiredObject(o.id, o.asset) for o in ground))
    verdict = SceneVerifier().verify(ground_spec, replace(state, objects=ground)) if ground else {'valid':True, 'failures':[]}
    failures = list(verdict['failures'])
    def fail(kind, *names, **values):
        failures.append(dict(type=kind, objects=list(names), **values))
    for p in packet['fixed']:
        if poses[p['id']] != p:
            fail('fixed_object_changed', p['id'])
    for name, options in packet['domains'].items():
        if poses[name] not in options:
            fail('outside_pose_domain', name)
    boxes = {name:bounds(spec, p) for name, p in poses.items()}
    for name, b in boxes.items():
        if any(b[i] < -1e-7 or b[i+3] > limit+1e-7
               for i, limit in enumerate((spec.room.width, spec.room.depth, spec.room.height))):
            fail('3d_bounds', name)
    for i, name in enumerate(boxes):
        for other in list(boxes)[i+1:]:
            if overlap(boxes[name], boxes[other]):
                fail('3d_collision', name, other)
    for name, rule in packet['requirements'].items():
        b = boxes[name]
        if not rule['height'][0]-1e-7 <= b[2] <= rule['height'][1]+1e-7:
            fail('height', name)
        r = packet['rooms'][rule['room']]
        if not (r[0]-1e-7 <= b[0] and r[1]-1e-7 <= b[1] and b[3] <= r[2]+1e-7 and b[4] <= r[3]+1e-7):
            fail('room_assignment', name)
        parent = rule['support']
        if parent == 'floor':
            supported = abs(b[2]) <= 1e-7
        else:
            a = boxes[parent]
            # Full footprint containment with 5 cm margin, contact at top face.
            supported = abs(b[2]-a[5]) <= 1e-7 and a[0]+.05-1e-7 <= b[0] and a[1]+.05-1e-7 <= b[1] and b[3] <= a[3]-.05+1e-7 and b[4] <= a[4]-.05+1e-7
        if not supported:
            fail('unsupported', name, parent)
    visibility = {}
    for name, rule in packet['visibility'].items():
        visibility[name] = visible_fraction(packet['view'], name, boxes)
        if not rule[0]-1e-7 <= visibility[name] <= rule[1]+1e-7:
            fail('camera_visibility', name, actual=visibility[name], required=rule)
    for relation in packet['distances']:
        a, b = boxes[relation['a']], boxes[relation['b']]
        distance = math.sqrt(sum(((a[i]+a[i+3]-b[i]-b[i+3])/2)**2 for i in range(3)))
        if not relation['min']-1e-7 <= distance <= relation['max']+1e-7:
            fail('joint_distance', relation['a'], relation['b'], actual=distance)
    path = navigation(packet, boxes)
    if path is None:
        fail('passage_blocked')
    physical = physics(spec, poses)
    if not physical:
        fail('physics_collision')
    return {'valid':verdict['valid'], 'success':not failures, 'failures':failures,
            'shortest_actions':len(path) if path is not None else None,
            'turns':sum(a != b for a, b in zip(path or [], (path or [])[1:])),
            'visibility':visibility, 'physical_valid':physical, 'scene':response}


def make_case(split, family, tier, index):
    """Constructive feasible layouts; candidate domains have no preferred ordering."""
    rng = random.Random(SEEDS[split]+FAMILIES.index(family)*10000+tier*1000+index)
    w, d = (10+2*tier, 8+2*tier)
    if split in ('selection', 'test'):
        d += 2 if split == 'test' else 1
    assets, fixed, witness, requirements, domains = {}, [], [], {}, {}
    visual_assets = {}
    def add(name, asset, size, x, y, z, support='floor', room='all', movable=True):
        assets[asset] = Asset(*size)
        visual_assets[asset] = 'crate' if 'box' in asset else 'obstacle'
        p = dict(id=name, asset=asset, x=x, y=y, z=z, yaw_deg=0)
        witness.append(p)
        if movable:
            requirements[name] = {'support':support, 'height':[z, z], 'room':room}
        else:
            fixed.append(p)
        return p
    rooms = {'all':[0, 0, w, d]}
    if family != 'support':
        rooms.update(left=[0, 0, w/2-.15, d], right=[w/2+.15, 0, w, d])
        for i, (y0, y1) in enumerate(((0, d/2-1.1), (d/2+1.1, d))):
            add('divider_'+str(i), 'wall_'+str(i), (.3, y1-y0, 2.8), w/2, (y0+y1)/2, 0, movable=False)
    # Two stacks per room, more simultaneous packing variables at higher tiers.
    slots = [(1.5, 1.5), (1.5, d-1.5), (w-1.5, 1.5), (w-1.5, d-1.5),
             (3.5, d-1.5), (w-3.5, d-1.5)]
    count = 3+tier
    for i, (x, y) in enumerate(slots[:count]):
        room = 'all' if family == 'support' else ('left' if x < w/2 else 'right')
        h = (.7, 1., 1.3)[i%3]
        base = add('base_'+str(i), 'stand_'+str(i%3), (1.4, 1.4, h), x, y, 0, room=room)
        top = add('top_'+str(i), 'box_'+str(i%2), (.7, .7, .6), x, y, h, base['id'], room)
        if tier > 0 and i%2 == 0:
            add('cap_'+str(i), 'box_cap', (.35, .35, .3), x, y, h+.6, top['id'], room)
    view = {'position':[1.5, -2., 1.4], 'target':[1.5, d/2, 1.4], 'fov':100.}
    if family == 'visibility':
        add('screen', 'screen', (2.1, .3, 2.7), 1.5, d/2, 0, movable=False)
    name = f'{split}_{family}_{tier}_{index}'
    spec = SceneSpec(name, Room(w, d, 4.5), tuple(RequiredObject(p['id'], p['asset']) for p in witness), assets)
    points = [(x+.5, y+.5) for x in range(1, w-1, 2) for y in range(1, d-1, 2)]
    # Some alternatives are intentionally locally plausible but globally block
    # passages, supporting surfaces or camera rays. All arms see identical domains.
    for p in witness:
        if p['id'] not in requirements:
            continue
        options = [p]
        points_copy = list(points)
        rng.shuffle(points_copy)
        for x, y in points_copy:
            q = dict(p, x=x, y=y, yaw_deg=rng.choice((0, 90, 180, 270)))
            if q not in options:
                options.append(q)
            if len(options) >= (8, 12, 18)[tier]:
                break
        # All descendants can follow all ancestor alternatives, so support is a
        # relational constraint rather than a unary fingerprint of the witness.
        domains[p['id']] = options
    for p in witness:
        if p['id'] not in requirements or requirements[p['id']]['support'] == 'floor':
            continue
        parent = requirements[p['id']]['support']
        for q in domains[parent]:
            candidate = dict(p, x=q['x'], y=q['y'], yaw_deg=q['yaw_deg'])
            if candidate not in domains[p['id']]:
                domains[p['id']].append(candidate)
    for options in domains.values():
        rng.shuffle(options)
    packet = {'width':w, 'depth':d, 'obstacles':len(fixed), 'spec':asdict(spec), 'fixed':fixed,
              'domains':domains, 'requirements':requirements, 'rooms':rooms, 'view':view,
              'visibility':{}, 'distances':[], 'start':[0, 0], 'goal':[w-1, d-1], 'visual_assets':visual_assets}
    boxes = {p['id']:bounds(spec, p) for p in witness}
    if family == 'visibility':
        packet['visibility'] = {'top_0':[.65, 1.], 'top_1':[0., .2]}
    # Joint 3D centre distances couple otherwise independent stacks. These are
    # ordinary public layout requirements, not a score fitted to model results.
    for i in range(count-1):
        a, b = boxes['top_'+str(i)], boxes['top_'+str(i+1)]
        distance = math.sqrt(sum(((a[j]+a[j+3]-b[j]-b[j+3])/2)**2 for j in range(3)))
        tolerance = (1.5, 1., .6)[tier]
        packet['distances'].append({'a':'top_'+str(i), 'b':'top_'+str(i+1),
                                    'min':max(0., distance-tolerance), 'max':distance+tolerance})
    # Anonymous labels and shuffled object order avoid exposing construction order.
    labels = list(boxes)
    rng.shuffle(labels)
    names = {key:'item_'+str(i) for i, key in enumerate(labels)}
    def rename(p):
        return dict(p, id=names[p['id']])
    packet['spec']['required_objects'] = [dict(p, id=names[p['id']]) for p in packet['spec']['required_objects']]
    rng.shuffle(packet['spec']['required_objects'])
    packet['fixed'] = [rename(p) for p in fixed]
    keys = list(domains)
    rng.shuffle(keys)
    packet['domains'] = {names[k]:[rename(p) for p in domains[k]] for k in keys}
    packet['requirements'] = {names[k]:dict(v, support=names.get(v['support'], v['support'])) for k, v in requirements.items()}
    packet['visibility'] = {names[k]:v for k, v in packet['visibility'].items()}
    packet['distances'] = [dict(r, a=names[r['a']], b=names[r['b']]) for r in packet['distances']]
    response = pack(name, [rename(p) for p in witness])
    case = {'id':name, 'tier':('easy', 'medium', 'hard')[tier], 'family':family, 'packet':packet}
    result = check(case, response)
    if not result['success']:
        raise AssertionError((name, result['failures']))
    return case, response


def pack(name, poses):
    return {'state':{'scene_id':name, 'objects':[{k:v for k, v in p.items() if k != 'z'} for p in poses]},
            'elevations':{p['id']:p['z'] for p in poses}}


def dataset(split, repeats=1):
    return [make_case(split, family, tier, index) for family in FAMILIES for tier in range(3) for index in range(repeats)]


def original(case):
    state = FixedSceneAgent().generate(SceneSpec.from_dict(case['packet']['spec']))
    return {'state':state.to_dict(), 'elevations':{o.id:0. for o in state.objects}}


def summarize(rows):
    solved = [r for r in rows if r['success']]
    return {'tasks':len(rows), 'valid':sum(r['valid'] for r in rows),
            'solvable':len(solved), 'solvable_rate':len(solved)/len(rows),
            'errors':dict(Counter(f['type'] for r in rows for f in r['failures'])),
            'shortest_actions_mean':statistics.mean(r['shortest_actions'] for r in solved) if solved else None,
            'first_output_seconds_mean':statistics.mean(r['generation_seconds'] for r in rows),
            'by_family':{family:{'tasks':sum(r['family'] == family for r in rows),
                                'solvable':sum(r['success'] and r['family'] == family for r in rows)}
                         for family in FAMILIES}}


def evaluate(source, cases, out, run):
    audit(source)
    out = Path(out)
    payload = out/'payload'
    payload.mkdir(parents=True, exist_ok=False)
    (payload/'layout.py').write_text(source)
    rows, resources = [], []
    for case in cases:
        row = {'id':case['id'], 'family':case['family'], 'tier':case['tier'], 'valid':False,
               'success':False, 'failures':[], 'shortest_actions':None, 'scene':None}
        process = LayoutProcess(payload, run)
        started = time.perf_counter()
        try:
            response = process.request(case['packet'])
            row['generation_seconds'] = time.perf_counter()-started
            row.update(check(case, response))
        except (ValueError, RuntimeError, TimeoutError, BrokenPipeError, KeyError, TypeError, OverflowError) as error:
            row.update(failures=[{'type':'execution_error', 'message':str(error)[:800]}],
                       generation_seconds=time.perf_counter()-started)
        finally:
            resources.append(process.close())
        rows.append(row)
    (out/'rows.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    report = {'metrics':summarize(rows), 'execution':resources, 'outputs_per_task':1}
    save(out/'summary.json', report)
    return report, rows
