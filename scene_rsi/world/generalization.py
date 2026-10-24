"""Frozen-map comparisons. Feasibility witnesses never enter proposal requests."""
from collections import deque
import json
from pathlib import Path
import random
import statistics
import time

from ..meta.layout_process import LayoutProcess
from .algorithm_experiment import audit, build_task, assess
from .generator_benchmark import diversity, save
from .planning import solve
from .task import DIRECTIONS, GeneratorConfig, make_task, transition


# Declared before looking at the frozen generator's scores. All remain in the
# original 4..8-cell, <=12-obstacle task domain, with new combinations/topologies.
CONDITIONS = (
    ('open', 4, 7, 4, 'random', 'easy'),
    ('wide', 5, 8, 8, 'random', 'easy'),
    ('large', 7, 7, 12, 'random', 'medium'),
    ('largest', 8, 8, 12, 'random', 'medium'),
    ('door', 4, 8, 10, 'wall', 'hard'),
    ('rooms', 7, 6, 12, 'cross', 'hard'),
    ('slalom', 5, 5, 10, 'slalom', 'hard'),
    ('dense_tall', 4, 5, 12, 'random', 'hard'),
    ('dense_wide', 5, 4, 12, 'random', 'hard'),
    ('islands', 4, 4, 12, 'random', 'hard'),
    ('separated', 6, 5, 12, 'separated', 'hard'),
    ('bars', 6, 7, 12, 'bars', 'hard'),
)


def cases(seed=710000, repeats=16, conditions=CONDITIONS):
    """Sample each obstacle map once; condition actors only on geometric distance."""
    result = []
    for group, (name, width, depth, count, topology, difficulty) in enumerate(conditions):
        cells = [(x, y) for y in range(depth) for x in range(width)]
        for index in range(repeats):
            number = seed + group*1000 + index
            rng = random.Random(number)
            obstacles = set()
            if topology == 'wall':
                door = rng.randrange(depth)
                obstacles = {(width//2, y) for y in range(depth) if y != door}
            elif topology == 'cross':
                obstacles = {(width//2, y) for y in range(depth)} | {(x, depth//2) for x in range(width)}
                obstacles -= {(width//2, 1), (1, depth//2)}
            elif topology == 'slalom':
                obstacles = {(x, y) for x in (1, 3) for y in range(depth) if y != (0 if x == 1 else depth-1)}
            elif topology == 'separated':
                obstacles = {(x, y) for x in (1, width-2) for y in range(depth)}
            elif topology == 'bars':
                obstacles = {(x, y) for y in (2, 4) for x in range(width) if x != (1 if y == 2 else width-2)}
            obstacles.update(rng.sample([c for c in cells if c not in obstacles], count-len(obstacles)))
            ordered = rng.sample(sorted(obstacles), count)
            free = [c for c in cells if c not in obstacles]
            eligible = [(c, g) for c in free for g in free if abs(c[0]-g[0])+abs(c[1]-g[1]) >= 2]
            if not eligible:
                raise ValueError('condition has no geometrically legal actor placement')
            crate, goal = rng.choice(eligible)
            agent = rng.choice([c for c in free if c not in (crate, goal)])
            result.append({'id':f'{name}_{index}', 'seed':number, 'condition':name,
                           'topology':topology, 'design_difficulty':difficulty,
                           'packet':{'width':width, 'depth':depth, 'obstacles':count,
                                     'sample':ordered+[agent, crate, goal]}})
    return result


def map_feasibility(packet):
    """Exact existential search, not a heuristic failure label.

    For each initial crate cell, seed the original transition graph with EVERY
    possible agent position. A reachable crate at Manhattan distance >=2 is a
    legal task witness; exhaustion for all crate cells proves none exists.
    The original independent solver verifies every positive witness separately.
    """
    width, depth = packet['width'], packet['depth']
    obstacles = {tuple(c) for c in packet['sample'][:-3]}
    free = [(x, y) for y in range(depth) for x in range(width) if (x, y) not in obstacles]
    task = make_task(width, depth, obstacles, *[tuple(c) for c in packet['sample'][-3:]])
    explored = 0
    for crate in free:
        roots = {(agent, crate):agent for agent in free if agent != crate}
        frontier = deque(roots)
        while frontier:
            state = frontier.popleft()
            explored += 1
            if abs(state[1][0]-crate[0])+abs(state[1][1]-crate[1]) >= 2:
                witness = make_task(width, depth, obstacles, roots[state], crate, state[1])
                checked = solve(witness)
                if checked['status'] != 'solvable':
                    raise AssertionError('existential witness disagrees with original solver')
                return {'status':'feasible', 'explored':explored, 'witness':witness.to_dict(),
                        'witness_steps':len(checked['actions'])}
            for action in DIRECTIONS:
                following, event = transition(task, state, action)
                if event != 'collision' and following not in roots:
                    roots[following] = roots[state]
                    frontier.append(following)
    return {'status':'impossible', 'explored':explored}


def route_metrics(task, actions):
    state, pushes = task.start, []
    for action in actions or []:
        state, event = transition(task, state, action)
        if event == 'push':
            pushes.append(action)
    return {'action_turns':sum(a != b for a, b in zip(actions or [], (actions or [])[1:])),
            'push_turns':sum(a != b for a, b in zip(pushes, pushes[1:]))}


def describe(rows):
    solved = [r for r in rows if r.get('solvability') == 'solvable']
    valid = [r for r in rows if r.get('valid')]
    def mean(key, subset=solved):
        values = [r[key] for r in subset if r.get(key) is not None]
        return statistics.mean(values) if values else None
    return {'tasks':len(rows), 'valid':len(valid), 'solvable':len(solved),
            'solvable_rate':len(solved)/len(rows) if rows else None,
            'errors':{key:sum(r.get('failure_type') == key for r in rows)
                      for key in sorted({r.get('failure_type') for r in rows if r.get('failure_type')})},
            'shortest_actions_mean':mean('reference_steps'), 'pushes_mean':mean('reference_pushes'),
            'action_turns_mean':mean('action_turns'), 'push_turns_mean':mean('push_turns'),
            'first_output_seconds_mean':mean('generation_wall_seconds', rows),
            'physics_replays':sum(r.get('reference_replayed') is True for r in solved),
            'fixed_agent_successes':sum(r.get('fixed_agent', {}).get('success') is True for r in rows),
            'difficulty_counts':{'2..5':sum(2 <= r['reference_steps'] <= 5 for r in solved),
                                 '6..12':sum(6 <= r['reference_steps'] <= 12 for r in solved),
                                 '13+':sum(r['reference_steps'] >= 13 for r in solved)},
            'diversity_by_condition':{
                name:diversity([r for r in valid if r['condition'] == name])
                for name in sorted({r['condition'] for r in valid})}}


def evaluate_cases(source, dataset, run, out):
    """One output per map, no fallback, rejection, solver call or score in payload."""
    audit(source)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    payload = out/'payload'
    payload.mkdir()
    (payload/'layout.py').write_text(source)
    rows, resources = [], []
    for offset in range(0, len(dataset), 16):
        process = LayoutProcess(payload, run)
        try:
            for case in dataset[offset:offset+16]:
                packet = case['packet']
                row = {k:v for k, v in case.items() if k != 'feasibility'}
                row.update(valid=False, generated=False, failures=[],
                           config={key:packet[key] for key in ('width', 'depth', 'obstacles')})
                started = time.perf_counter()
                try:
                    response = process.request(packet)
                    config = GeneratorConfig(packet['width'], packet['depth'], packet['obstacles'])
                    task = build_task(response, packet['sample'], config, case['seed'], 0)
                    row['generation_wall_seconds'] = time.perf_counter()-started
                    if task is None:
                        raise ValueError('minimum crate-goal distance violated; no resampling')
                    assess(task, row)
                    reference = solve(task)
                    row.update(route_metrics(task, reference['actions']))
                    row['failure_type'] = None if row['solvability'] == 'solvable' else row['solvability']
                except (ValueError, RuntimeError, TimeoutError, BrokenPipeError, KeyError, TypeError) as error:
                    row.update(failure_type='generation_error', error=str(error)[:1000],
                               generation_wall_seconds=time.perf_counter()-started)
                rows.append(row)
        finally:
            resources.append(process.close())
    (out/'rows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    report = {'all':describe(rows), 'groups':{name:describe([r for r in rows if r['condition'] == name])
                                          for name in sorted({r['condition'] for r in rows})},
              'execution':resources, 'outputs_per_map':1, 'oracle_rejection_calls':0}
    if dataset and 'feasibility' in dataset[0]:
        feasible_ids = {c['id'] for c in dataset if c['feasibility']['status'] == 'feasible'}
        report['feasible_maps'] = describe([r for r in rows if r['id'] in feasible_ids])
        report['impossible_maps'] = describe([r for r in rows if r['id'] not in feasible_ids])
    save(out/'summary.json', report)
    return report, rows
