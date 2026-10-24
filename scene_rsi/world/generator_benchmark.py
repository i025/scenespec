"""Measure the existing trusted generator; never import or execute a candidate."""
from collections import Counter
from dataclasses import asdict, replace
from itertools import combinations
import json
import math
from pathlib import Path
import random
import statistics
import time
from unittest.mock import patch

from ..benchmark.runner import git_provenance
from ..evaluator.verifier import SceneVerifier
from . import planning, task as tasks
from .experiments import evaluate

# Frozen before any proposal. Different dimensions/densities as well as seeds.
CONDITIONS = {
    'dev': [(6, 6, 6), (7, 5, 8), (5, 7, 5)],
    'val': [(4, 4, 3), (6, 8, 10), (8, 6, 12)],
}
SEEDS = {'dev': 2000, 'val': 90000}


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def measure(seed, config):
    """Observe Random.sample and filter searches without changing their results.

    This uses the original generate(), including its RNG stream and 256-attempt
    limit. Observation overhead is included in generation time. No parallel calls
    are allowed while these two module attributes are temporarily patched.
    """
    draws, searches = [], []
    original_solve = planning.solve

    class ObservedRandom(random.Random):
        def sample(self, population, k, **kwargs):
            result = super().sample(population, k, **kwargs)
            draws.append(result)
            return result

    def observe_search(task):
        started = time.perf_counter()
        result = original_solve(task)
        searches.append({'status': result['status'], 'seconds': time.perf_counter()-started})
        return result

    task, error = None, None
    started, cpu = time.perf_counter(), time.process_time()
    with patch.object(tasks.random, 'Random', ObservedRandom), patch.object(planning, 'solve', observe_search):
        try:
            task = tasks.generate(seed, config)
        except ValueError as exception:
            error = str(exception)
    generation_wall = time.perf_counter()-started
    generation_cpu = time.process_time()-cpu
    close, corners = 0, 0
    corner_cells = {(0, 0), (0, config.depth-1), (config.width-1, 0), (config.width-1, config.depth-1)}
    for sample in draws:
        crate, goal = sample[-2:]
        if sum(abs(a-b) for a, b in zip(crate, goal)) < 2:
            close += 1
        elif config.avoid_corners and crate in corner_cells:
            corners += 1
    rejected = {'minimum_distance': close, 'corner_option': corners,
                'unsolvable': sum(row['status'] == 'unsolvable' for row in searches),
                'unknown': sum(row['status'] == 'unknown' for row in searches)}
    row = {'seed': seed, 'config': asdict(config), 'generated': task is not None,
           'generation_error': error, 'sample_attempts': len(draws),
           'rejections': rejected, 'rejected_attempts': sum(rejected.values()),
           'filter_searches': len(searches),
           'filter_search_seconds': sum(row['seconds'] for row in searches),
           'generation_wall_seconds': generation_wall, 'generation_cpu_seconds': generation_cpu}
    if task is None:
        return row
    verification = SceneVerifier().verify(task.spec, task.initial)
    assessment = evaluate(task)  # Existing oracle + fixed Agent + actual Bullet reference replay.
    state, reference_pushes = task.start, 0
    for action in assessment['reference_actions'] or []:
        state, event = tasks.transition(task, state, action)
        reference_pushes += event == 'push'
    row.update(scene=task.to_dict(), valid=verification['valid'], failures=verification['failures'],
               solvability=assessment['solvability'], reference_replayed=assessment['reference_replayed'],
               reference_steps=(len(assessment['reference_actions'])
                                if assessment['reference_actions'] is not None else None),
               reference_pushes=(reference_pushes if assessment['solvability'] == 'solvable' else None),
               crate_goal_manhattan=sum(abs(a-b) for a, b in zip(task.start[1], task.goal)),
               search_states=assessment['search_states'],
               fixed_agent={key: assessment[key] for key in
                            ('outcome', 'success', 'steps', 'pushes', 'collisions', 'timeout')},
               assessment_wall_seconds=assessment['wall_seconds'])
    return row


def distribution(values):
    values = [value for value in values if value is not None]
    return {'count': len(values), 'mean': statistics.mean(values) if values else None,
            'median': statistics.median(values) if values else None,
            'min': min(values) if values else None, 'max': max(values) if values else None}


def diversity(rows):
    """Within one geometry condition. Coordinates are compared directly, no digests."""
    layouts, obstacles, crate_cells = [], [], []
    for row in rows:
        if not row['generated']:
            continue
        task = tasks.WorldTask.from_dict(row['scene'])
        layouts.append((tuple(sorted(task.obstacles)), task.start, task.goal))
        obstacles.append(task.obstacles)
        crate_cells.append(task.start[1])
    distances = [1-len(a & b)/len(a | b) if a | b else 0
                 for a, b in combinations(obstacles, 2)]
    counts = Counter(crate_cells)
    entropy = -sum((count/len(crate_cells))*math.log2(count/len(crate_cells)) for count in counts.values())
    config = rows[0]['config']
    return {'unique_layouts': len(set(layouts)),
            'unique_fraction': len(set(layouts))/len(layouts) if layouts else None,
            'obstacle_cell_coverage': len(set().union(*obstacles))/(config['width']*config['depth']),
            'mean_pairwise_obstacle_jaccard_distance': statistics.mean(distances) if distances else None,
            'crate_cell_entropy_bits': entropy if crate_cells else None}


def summarize(rows):
    generated = [row for row in rows if row['generated']]
    valid = [row for row in generated if row['valid']]
    solvable = [row for row in valid if row['solvability'] == 'solvable']
    total = len(rows)
    attempts = sum(row['sample_attempts'] for row in rows)
    return {'requests': total, 'generated': len(generated), 'valid': len(valid),
            'solvable': len(solvable), 'generation_valid_rate': len(valid)/total,
            'solvable_per_request': len(solvable)/total,
            'solvable_given_valid': len(solvable)/len(valid) if valid else None,
            'physics_reference_successes': sum(row['reference_replayed'] is True for row in solvable),
            'solvability_unknown': sum(row['solvability'] == 'unknown' for row in valid),
            'fixed_agent_successes': sum(row['fixed_agent']['success'] for row in valid),
            'fixed_agent_success_per_request': sum(row['fixed_agent']['success'] for row in valid)/total,
            'fixed_agent_outcomes': dict(Counter(row['fixed_agent']['outcome'] for row in valid)),
            'fixed_agent_collisions': sum(row['fixed_agent']['collisions'] for row in valid),
            'fixed_agent_timeouts': sum(row['fixed_agent']['timeout'] for row in valid),
            'sample_attempts': attempts,
            'accepted_per_sample_attempt': len(generated)/attempts if attempts else None,
            'rejected_attempts': sum(row['rejected_attempts'] for row in rows),
            'rejection_causes': {key: sum(row['rejections'][key] for row in rows)
                                 for key in ('minimum_distance', 'corner_option', 'unsolvable', 'unknown')},
            'attempts_per_request': distribution([row['sample_attempts'] for row in rows]),
            'shortest_action_steps_given_solvable': distribution([row['reference_steps'] for row in solvable]),
            'reference_pushes_given_solvable': distribution([row['reference_pushes'] for row in solvable]),
            'push_detour_given_solvable': distribution([row['reference_pushes']-row['crate_goal_manhattan']
                                                       for row in solvable]),
            'oracle_search_states': distribution([row['search_states'] for row in valid]),
            'generation_wall_seconds': distribution([row['generation_wall_seconds'] for row in rows]),
            'generation_cpu_seconds_total': sum(row['generation_cpu_seconds'] for row in rows),
            'filter_search_seconds_total': sum(row['filter_search_seconds'] for row in rows),
            'assessment_wall_seconds_total': sum(row.get('assessment_wall_seconds', 0) for row in rows)}


def run(out, samples=32):
    """Baseline only. No callback, candidate path, arbitrary import or live model call."""
    if type(samples) is not int or not 1 <= samples <= 128:
        raise ValueError('samples must be an integer in [1, 128]')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    groups, aggregates = [], {}
    for split, conditions in CONDITIONS.items():
        directory = out/split
        directory.mkdir()
        split_rows = []
        for w, d, count in conditions:
            for require in (False, True):
                config = tasks.GeneratorConfig(w, d, count, require_solvable=require)
                rows = [measure(seed, config) for seed in range(SEEDS[split], SEEDS[split]+samples)]
                name = f'{w}x{d}_{count}_' + ('filtered' if require else 'unfiltered')
                with (directory/(name+'.jsonl')).open('w') as stream:
                    for row in rows:
                        stream.write(json.dumps(row, allow_nan=False)+'\n')
                group = {'split': split, 'condition': asdict(config), 'metrics': summarize(rows),
                         'diversity': diversity(rows)}
                groups.append(group)
                split_rows.extend(rows)
                print(f'{split}/{name}: {group["metrics"]["solvable"]}/{samples} solvable', flush=True)
        aggregates[split] = {label: summarize([row for row in split_rows
                                              if row['config']['require_solvable'] == require])
                             for label, require in (('unfiltered', False), ('filtered', True))}
    report = {'baseline_commit': '34d2300', 'controller_git': git_provenance(), 'samples_per_condition': samples,
              'conditions': CONDITIONS, 'seed_starts': SEEDS, 'groups': groups, 'aggregates': aggregates,
              'selection': {'status': 'not_run', 'selected': None, 'maximum_candidates': 3,
                            'model_calls': 0, 'candidate_executions': 0,
                            'model_tokens': None, 'model_cost': None},
              'scope': 'existing trusted generator only; filtering is not an algorithm modification'}
    save(out/'summary.json', report)
    # This is the only artifact intended for the improvement model: no val data,
    # reference actions, hand-written failure analysis or reference solutions.
    save(out/'dev_feedback.json', {'baseline_commit': '34d2300',
                                  'groups': [group for group in groups if group['split'] == 'dev']})
    return report
