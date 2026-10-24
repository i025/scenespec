"""Finite-domain indoor tasks with fixed destinations and independently checked paths."""
from collections import Counter
from dataclasses import asdict, replace
import json
from pathlib import Path
import random
import statistics
import time

import pybullet as bullet

from ..evaluator.verifier import SceneVerifier
from ..geometry import footprint, intersects, rect_region
from ..meta.layout_process import LayoutProcess
from ..schema import Asset, Constraint, PlacedObject, Region, RequiredObject, Room, SceneSpec, SceneState, AccessPath
from ..world.algorithm_experiment import audit
from ..world.generator_benchmark import save
from ..world.planning import walk


SIZES = {'dev':((7, 8), (8, 9), (10, 10)),
         'selection':((8, 8), (9, 9), (10, 11)),
         'test':((7, 9), (9, 10), (11, 11))}
SEEDS = {'dev':1200000, 'selection':1300000, 'test':1400000}


def navigation(spec, state, start, goal):
    """Original single-agent walk BFS on cell centres, with .15 m clearance."""
    rects = [footprint(obj, spec.assets[obj.asset]) for obj in state.objects]
    free = {(x, y) for y in range(int(spec.room.depth)) for x in range(int(spec.room.width))
            if not any(a-.15 < x+.5 < c+.15 and b-.15 < y+.5 < d+.15 for a, b, c, d in rects)}
    class Grid:
        def free(self, cell):
            return cell in free
    if tuple(start) not in free or tuple(goal) not in free:
        return None
    return walk(Grid(), tuple(start), tuple(goal), (-100, -100))


def physical_check(spec, state):
    """Original axis-aligned assets represented as PyBullet boxes, outside candidates."""
    client = bullet.connect(bullet.DIRECT)
    try:
        bodies = []
        for obj in state.objects:
            asset = spec.assets[obj.asset]
            shape = bullet.createCollisionShape(bullet.GEOM_BOX,
                     halfExtents=[asset.width/2, asset.depth/2, asset.height/2], physicsClientId=client)
            orientation = bullet.getQuaternionFromEuler([0, 0, obj.yaw_deg*3.141592653589793/180])
            bodies.append(bullet.createMultiBody(baseMass=0, baseCollisionShapeIndex=shape,
                          basePosition=[obj.x, obj.y, asset.height/2], baseOrientation=orientation,
                          physicsClientId=client))
        return not any(any(point[8] < -1e-6 for point in bullet.getClosestPoints(a, b, 0, physicsClientId=client))
                       for i, a in enumerate(bodies) for b in bodies[i+1:])
    finally:
        bullet.disconnect(client)


def rotate(packet, witness, turns):
    walls = {'north':'west', 'west':'south', 'south':'east', 'east':'north'}
    for _ in range(turns):
        width, depth = packet['width'], packet['depth']
        def pose(obj):
            return dict(obj, x=depth-obj['y'], y=obj['x'], yaw_deg=(obj['yaw_deg']+90)%360)
        spec = packet['spec']
        spec['room']['width'], spec['room']['depth'] = depth, width
        for region in spec['regions']:
            region['x'], region['y'], region['width'], region['depth'] = (
                depth-region['y']-region['depth'], region['x'], region['depth'], region['width'])
        for constraint in spec['constraints']:
            if constraint['type'] == 'near_wall':
                constraint['wall'] = walls[constraint['wall']]
        packet['fixed'] = [pose(obj) for obj in packet['fixed']]
        packet['domains'] = {name:[pose(obj) for obj in values] for name, values in packet['domains'].items()}
        for name in ('start', 'goal'):
            x, y = packet[name]
            packet[name] = [depth-1-y, x]
        packet['width'], packet['depth'] = depth, width
        witness = SceneState(witness.scene_id, tuple(PlacedObject(**pose(asdict(obj))) for obj in witness.objects))
    return packet, witness


def make_case(split, tier, index):
    """Construct a hidden feasible layout, then obscure it among pose domains."""
    width, depth = SIZES[split][tier]
    rng = random.Random(SEEDS[split]+tier*1000+index)
    assets = {'partition':Asset(.8, 2.8, 1.4), 'desk':Asset(1.8, .8, .75),
              'chair':Asset(.8, .8, .9), 'cabinet':Asset(1.8, .8, 1.8), 'column':Asset(.8, .8, 2.4)}
    fixed = [PlacedObject('target_desk', 'desk', width-1, depth-.5, 180)]
    movable = [PlacedObject('seat', 'chair', width-1.5, depth-1.5, 0),
               PlacedObject('corner_storage', 'cabinet', 1, depth-.5, 0)]
    constraints = [Constraint('near_wall', 'target_desk', wall='north', max_gap=.15),
                   Constraint('near_wall', 'target_desk', wall='east', max_gap=.15),
                   Constraint('near_wall', 'corner_storage', wall='north', max_gap=.15),
                   Constraint('near_wall', 'corner_storage', wall='west', max_gap=.15),
                   Constraint('in_front_of', 'seat', 'target_desk', max_gap=.25)]
    for row, y in enumerate((2, 4, 6) if tier == 2 else (2, 4)):
        start_x = row%2
        number = (width-1)//3
        parts = []
        for j in range(number):
            obj = PlacedObject(f'partition_{row}_{j}', 'partition', start_x+3*j+1.5, y+.5, 270 if row%2 else 90)
            movable.append(obj)
            parts.append(obj.id)
        for x in range(start_x+3*number, start_x+width-1):
            fixed.append(PlacedObject(f'column_{row}_{x}', 'column', x+.5, y+.5, 0))
        if tier:
            subject, reference = (parts[1], parts[0]) if row%2 else (parts[0], parts[1])
            constraints.append(Constraint('in_front_of', subject, reference, max_gap=.25))
    regions = (Region('entry', 0, 0, 1, 1), Region('destination', width-2, depth-3, 1, 1))
    constraints.extend(Constraint('keep_clear', region=r.id) for r in regions)
    required = [RequiredObject(o.id, o.asset) for o in fixed+movable]
    rng.shuffle(required)
    name = f'{split}_{tier}_{index}'
    spec = SceneSpec(name, Room(width, depth, 3), tuple(required), assets, regions, tuple(constraints),
                     (AccessPath('entry', 'target_desk', .15, .8),))
    witness = SceneState(name, tuple(fixed+movable))
    packet = {'width':width, 'depth':depth, 'obstacles':len(fixed), 'spec':asdict(spec),
              'fixed':[asdict(o) for o in fixed], 'domains':{}, 'start':[0, 0], 'goal':[width-2, depth-3]}
    zones = [rect_region(r) for r in regions]
    fixed_rects = [footprint(o, assets[o.asset]) for o in fixed]
    for obj in movable:
        options = []
        for yaw in (0, 90, 180, 270):
            a = assets[obj.asset]
            nx, ny = (round(a.width), round(a.depth)) if yaw%180 == 0 else (round(a.depth), round(a.width))
            for y in range(depth-ny+1):
                for x in range(width-nx+1):
                    pose = PlacedObject(obj.id, obj.asset, x+nx/2, y+ny/2, yaw)
                    rect = footprint(pose, a)
                    if not any(intersects(rect, other) for other in fixed_rects+zones):
                        options.append(pose)
        # Coordinated alternatives allow several valid local relations, rather
        # than identifying a single witness through unary constraint filtering.
        chosen = [obj]
        for dy in (-1, 1):
            other = replace(obj, y=obj.y+dy)
            if other in options:
                chosen.append(other)
        rng.shuffle(options)
        for other in options:
            if other not in chosen:
                chosen.append(other)
            if len(chosen) == (8, 12, 16)[tier]:
                break
        rng.shuffle(chosen)
        packet['domains'][obj.id] = [asdict(o) for o in chosen]
    packet, witness = rotate(packet, witness, rng.randrange(4))
    spec = SceneSpec.from_dict(packet['spec'])
    path = navigation(spec, witness, packet['start'], packet['goal'])
    if path is None:
        raise AssertionError('constructive route failed')
    manhattan = sum(abs(a-b) for a, b in zip(packet['start'], packet['goal']))
    packet['minimum_steps'] = max(manhattan+4, len(path)-(8, 2, 0)[tier])
    labels = [r['id'] for r in packet['spec']['required_objects']]
    rng.shuffle(labels)
    names = {label:f'item_{i}' for i, label in enumerate(labels)}
    for obj in packet['spec']['required_objects']:
        obj['id'] = names[obj['id']]
    for constraint in packet['spec']['constraints']:
        for field in ('subject', 'reference'):
            if constraint[field]:
                constraint[field] = names[constraint[field]]
    for access in packet['spec']['access_paths']:
        access['to_object'] = names[access['to_object']]
    packet['fixed'] = [dict(o, id=names[o['id']]) for o in packet['fixed']]
    packet['domains'] = {names[name]:[dict(o, id=names[name]) for o in poses]
                         for name, poses in packet['domains'].items()}
    witness = replace(witness, objects=tuple(replace(o, id=names[o.id]) for o in witness.objects))
    case = {'id':name, 'tier':('easy', 'medium', 'hard')[tier], 'packet':packet}
    result = check(case, witness.to_dict())
    if not result['success']:
        raise AssertionError((name, result['failures']))
    return case, witness


def dataset(split, repeats=4):
    return [make_case(split, tier, index) for tier in range(3) for index in range(repeats)]


def check(case, response):
    packet = case['packet']
    spec = SceneSpec.from_dict(packet['spec'])
    state = SceneState.from_dict(response)
    verdict = SceneVerifier().verify(spec, state)
    failures = list(verdict['failures'])
    poses = {o.id:asdict(o) for o in state.objects}
    for obj in packet['fixed']:
        if poses.get(obj['id']) != obj:
            failures.append({'type':'fixed_object_changed', 'objects':[obj['id']]})
    for name, domain in packet['domains'].items():
        if poses.get(name) not in domain:
            failures.append({'type':'outside_pose_domain', 'objects':[name]})
    path, physical = None, None
    if verdict['valid']:
        path = navigation(spec, state, packet['start'], packet['goal'])
        physical = physical_check(spec, state)
        if not physical:
            failures.append({'type':'physics_collision'})
        if path is None:
            failures.append({'type':'fixed_destination_unreachable'})
        elif len(path) < packet['minimum_steps']:
            failures.append({'type':'path_too_short', 'actual':len(path), 'minimum':packet['minimum_steps']})
    return {'valid':verdict['valid'], 'success':not failures, 'failures':failures,
            'shortest_actions':len(path) if path is not None else None,
            'turns':sum(a != b for a, b in zip(path or [], (path or [])[1:])),
            'physical_valid':physical, 'scene':state.to_dict()}


def summarize(rows):
    success = sum(r['success'] for r in rows)
    costs = [r['generation_seconds'] for r in rows if r.get('generation_seconds') is not None]
    solved = [r for r in rows if r['success']]
    return {'tasks':len(rows), 'valid':sum(r['valid'] for r in rows), 'solvable':success,
            'solvable_rate':success/len(rows),
            'errors':dict(Counter(f['type'] for r in rows for f in r['failures'])),
            'shortest_actions_mean':statistics.mean(r['shortest_actions'] for r in solved) if solved else None,
            'action_turns_mean':statistics.mean(r['turns'] for r in solved) if solved else None,
            'first_output_seconds_mean':statistics.mean(costs) if costs else None,
            'physics_checks':sum(r.get('physical_valid') is not None for r in rows),
            'physics_passes':sum(r.get('physical_valid') is True for r in rows),
            'by_tier':{tier:{'tasks':sum(r['tier'] == tier for r in rows),
                            'successes':sum(r['success'] and r['tier'] == tier for r in rows)}
                       for tier in sorted({r['tier'] for r in rows})}}


def evaluate(source, cases, out, run):
    audit(source)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    payload = out/'payload'
    payload.mkdir()
    (payload/'layout.py').write_text(source)
    rows, resources = [], []
    for case in cases:
        row = {'id':case['id'], 'tier':case['tier'], 'valid':False, 'success':False,
               'shortest_actions':None, 'failures':[], 'scene':None}
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
    report = {'metrics':summarize(rows), 'execution':resources, 'outputs_per_task':1,
              'resource_scope':'one fresh restricted process per task; CPU includes Apptainer, RSS lifetime high-water'}
    save(out/'summary.json', report)
    return report, rows
