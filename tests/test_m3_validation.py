"""Offline scheduling, evidence-budget and held-out feedback boundaries."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import run_m3_validation as experiment


class M3ValidationTests(unittest.TestCase):
    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        experiment.save(path, value)

    def metrics(self, preference=.3, successes=48):
        return {'runtime_errors': 0, 'successes': successes, 'visibility_mean': 1.,
                'diversity_position_rms_m': 1., 'diversity_rotation_deg': 90.,
                'valid_pair_count': 36, 'valid_pair_diversity_m': 1.,
                'preference_error_m': preference}

    def task(self):
        return {'budget': deepcopy(experiment.BUDGET), 'feedback': {
            'metrics': {'same_original_pool': True}, 'examples': [{'row': i} for i in range(8)]}}

    def formal_files(self, root):
        calls = []
        for initial in experiment.INITIALS:
            base = root / initial
            base.mkdir()
            experiment.save(base / 'private_tasks.json', {'dev': [], 'selection': [], 'test': []})
            self.save(base / 'baseline/selection/summary.json', self.metrics())
        for initial, repeat, policy in experiment.slots():
            folder = root / initial / policy / f'r{repeat}'
            folder.mkdir(parents=True)
            experiment.save(folder / 'task.json', {'initial': initial, 'repeat': repeat, 'policy': policy})
            calls.append({'initial': initial, 'repeat': repeat, 'policy': policy,
                          'K': 1, 'status': 'reserved', 'selected': False})
        experiment.save(root / 'calls.json', calls)
        experiment.save(root / 'ablation_plan.json', {'coding_tasks': 0})

    def ablation_files(self, root, changes, counts=None):
        counts = counts or {}
        records = {}
        names = ('full', 'history_separation', 'probe_delivery', 'constraint_margins')
        for name in names:
            path = root / 'offline_ablation' / name / 'task.json'
            task = self.task()
            task['arm'] = name
            self.save(path, task)
            records[name] = {'example_count': counts.get(name, 8), 'coding_task_path': str(path)}
        result = {'actual_dev': records, 'comparisons': {'actual_dev': {
            name: {'nontext_behavior_changed': changes.get(name, False)} for name in names[1:]}}}
        experiment.save(root / 'offline_ablation/result.json', result)

    def test_eight_fixed_slots_share_repeats_and_reverse_policy_order(self):
        slots = experiment.slots()
        self.assertEqual(len(slots), 8)
        self.assertEqual(len(set(slots)), 8)
        for initial in experiment.INITIALS:
            group = [(repeat, policy) for name, repeat, policy in slots if name == initial]
            self.assertEqual(group, [(0, 'M2'), (0, 'M3'), (1, 'M3'), (1, 'M2')])

    def test_all_proposals_and_selections_close_before_any_test_without_reselection(self):
        backend = Mock()
        proposed, phases = [], []
        failed = (experiment.INITIALS[1], 1, 'M2')
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.formal_files(root)

            def generate(task, folder, received_backend):
                self.assertIs(received_backend, backend)
                key = (task['initial'], task['repeat'], task['policy'])
                proposed.append(key)
                if key == failed:
                    return {'status': 'failed', 'usage': {'total_tokens': 7}}, None
                source = 'candidate_' + str(len(proposed))
                return {'status': 'completed', 'usage': {'total_tokens': 12}}, source

            def evaluate(source, cases, folder, *args):
                self.assertEqual(len(proposed), 8)
                phases.append(folder.name)
                if folder.name == 'test':
                    marker = experiment.read(root / 'proposals_complete.json')
                    self.assertTrue(marker['all_eight_closed'])
                    self.assertTrue(marker['selection_fixed'])
                    calls = experiment.read(root / 'calls.json')
                    self.assertTrue(all(c['status'] in ('evaluated', 'candidate_failed') for c in calls))
                    self.assertEqual(sum(c['selected'] for c in calls), 4)
                    if folder.parent.name == 'baseline':
                        return self.metrics(), []
                    # Accepted M3 proposals degrade on test; unselected M2 proposals
                    # improve. Neither held-out outcome may change the selection.
                    return self.metrics(.8 if folder.parent.parent.name == 'M3' else .01), []
                accepted = folder.parent.parent.name == 'M3'
                return self.metrics(.1 if accepted else .5), []

            with patch.object(experiment, 'candidate', side_effect=generate) as model, \
                    patch.object(experiment, 'evaluate4', side_effect=evaluate):
                experiment.execute(root, root, backend)
                with self.assertRaises(RuntimeError):
                    experiment.execute(root, root, backend)
                self.assertEqual(model.call_count, 8)
            self.assertEqual(set(proposed), set(experiment.slots()))
            first_test = phases.index('test')
            self.assertEqual(phases[:first_test], ['dev', 'selection'] * 7)
            self.assertEqual(phases[first_test:], ['test'] * 9)
            calls = experiment.read(root / 'calls.json')
            self.assertEqual(sum(c['status'] == 'candidate_failed' for c in calls), 1)
            self.assertEqual(sum(c['selected'] for c in calls), 4)
            scores = experiment.read(root / 'scores.json')
            self.assertEqual(len(scores), 8)
            for score in scores:
                if score['policy'] == 'M3':
                    self.assertTrue(score['selected'])
                    self.assertFalse(score['heldout_multiobjective_guard'])
                    self.assertEqual(score['selected_after']['preference_error_m'], .8)
                    self.assertAlmostEqual(score['selected_preference_improvement_m'], -.5)
                else:
                    self.assertFalse(score['selected'])
                    self.assertEqual(score['selected_after']['preference_error_m'], .3)
            self.assertEqual(experiment.read(root / 'finished.json')['extra_formal_trials'], 0)

    def test_ablation_plan_uses_actual_behavior_eight_examples_and_no_formal_scores(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.ablation_files(root, {'history_separation': True, 'probe_delivery': True,
                                      'constraint_margins': True}, {'history_separation': 7})
            experiment.save(root / 'scores.json', [{'policy': 'M3', 'selected': True}])
            experiment.plan_ablation(root)
            plan = experiment.read(root / 'ablation_plan.json')
            self.assertEqual(plan['selected_mechanism'], 'probe_delivery')
            self.assertEqual(plan['coding_tasks'], 2)
            self.assertFalse(plan['formal_outcomes_used'])
            calls = experiment.read(root / 'ablation_calls.json')
            self.assertEqual([c['arm'] for c in calls], ['full', 'removed'])
            self.assertEqual(experiment.read(root / 'ablations/removed/task.json')['arm'], 'probe_delivery')
            for call in calls:
                task = experiment.read(root / call['directory'] / 'task.json')
                self.assertEqual(task['budget'], experiment.BUDGET)
                self.assertEqual(len(task['feedback']['examples']), 8)

    def test_text_only_or_no_actual_dev_mechanism_change_causes_zero_model_calls(self):
        backend = Mock()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.ablation_files(root, {})
            result = experiment.read(root / 'offline_ablation/result.json')
            result['comparisons']['synthetic'] = {'history_separation': {'nontext_behavior_changed': True}}
            result['actual_dev']['history_separation']['prompt_changed'] = True
            experiment.save(root / 'offline_ablation/result.json', result)
            experiment.plan_ablation(root)
            self.assertEqual(experiment.read(root / 'ablation_calls.json'), [])
            self.assertEqual(experiment.read(root / 'ablation_plan.json')['coding_tasks'], 0)
            experiment.save(root / 'finished.json', {'formal_coding_tasks': 8})
            self.save(root / 'ablation_dev/private_tasks.json', {'dev': []})
            self.save(root / 'ablation_dev/baseline/dev/summary.json', self.metrics())
            with patch.object(experiment, 'candidate') as model, patch.object(experiment, 'evaluate4') as evaluator:
                experiment.execute_ablation(root, root, backend)
                model.assert_not_called()
                evaluator.assert_not_called()
            backend.assert_not_called()
            self.assertEqual(experiment.read(root / 'ablation_started.json')['tasks'], 0)
            self.assertEqual(experiment.read(root / 'ablation_results.json')['arms'], [])

    def test_prepare_compares_repeat_tasks_using_the_real_json_interface(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source_dir = base / 'sources'
            source_dir.mkdir()
            for initial in experiment.INITIALS:
                (source_dir / f'initial_{initial}.py').write_text('# offline source only\n')
            policy_paths = {name: base / (name + '.py') for name in experiment.POLICIES}
            for path in policy_paths.values():
                path.write_text('# offline frozen policy\n')
            task = self.task()
            task['feedback']['delivery'] = {'selected': [(0, 0), (2, 3)],
                                             'delivered': [(0, 0), (2, 3)]}

            def conditions(folder, geometry, counts):
                folder.mkdir()
                return {split: [] for split in counts}

            with patch.object(experiment, 'ROOT', base), \
                    patch.object(experiment, 'EXP', source_dir), \
                    patch.object(experiment, 'POLICIES', policy_paths), \
                    patch.object(experiment, 'git', return_value='offline'), \
                    patch.object(experiment, 'conditions', side_effect=conditions), \
                    patch.object(experiment, 'evaluate4', return_value=(self.metrics(), [])), \
                    patch.object(experiment, 'request_for', side_effect=lambda *args: (deepcopy(task), {})) as request, \
                    patch.object(experiment, 'candidate') as model, \
                    patch.object(experiment, 'run_codex') as backend:
                experiment.prepare(base / 'run', base)
                self.assertEqual(request.call_count, 8)
                model.assert_not_called()
                backend.assert_not_called()
            self.assertEqual(len(experiment.read(base / 'run/calls.json')), 8)
            for initial in experiment.INITIALS:
                for policy in experiment.POLICIES:
                    folder = base / 'run' / initial / policy
                    first = experiment.read(folder / 'r0/task.json')
                    self.assertEqual(first, experiment.read(folder / 'r1/task.json'))
                    self.assertEqual(first['feedback']['delivery']['selected'], [[0, 0], [2, 3]])
            self.assertEqual(task['feedback']['delivery']['selected'], [(0, 0), (2, 3)])

    def test_prepare_rejects_changed_evidence_budgets_repeats_and_original_pool(self):
        for violation in ('extra_example', 'missing_example', 'budget', 'repeat', 'metrics_pool'):
            with self.subTest(violation=violation), TemporaryDirectory() as directory:
                base = Path(directory)
                source_dir = base / 'sources'
                source_dir.mkdir()
                for initial in experiment.INITIALS:
                    (source_dir / f'initial_{initial}.py').write_text('# offline source only\n')
                policy_paths = {name: base / (name + '.py') for name in experiment.POLICIES}
                for path in policy_paths.values():
                    path.write_text('# offline frozen policy\n')

                def conditions(folder, geometry, counts):
                    folder.mkdir()
                    return {split: [] for split in counts}

                def request(source, cases, rows, policy, out, run, required):
                    task = self.task()
                    if violation == 'extra_example':
                        task['feedback']['examples'].append({'extra': 'must not be truncated'})
                    elif violation == 'missing_example':
                        task['feedback']['examples'].pop()
                    elif violation == 'budget':
                        task['budget']['total_tokens'] += 1
                    elif violation == 'repeat' and out.parent.name == 'r1':
                        task['different_repeat_task'] = True
                    elif violation == 'metrics_pool' and out.parent.parent.name == 'M3':
                        task['feedback']['metrics']['same_original_pool'] = False
                    return task, {}

                with patch.object(experiment, 'ROOT', base), \
                        patch.object(experiment, 'EXP', source_dir), \
                        patch.object(experiment, 'POLICIES', policy_paths), \
                        patch.object(experiment, 'git', return_value='offline'), \
                        patch.object(experiment, 'conditions', side_effect=conditions), \
                        patch.object(experiment, 'evaluate4', return_value=(self.metrics(), [])), \
                        patch.object(experiment, 'request_for', side_effect=request):
                    expected = ValueError if violation.endswith('example') else AssertionError
                    with self.assertRaises(expected):
                        experiment.prepare(base / 'run', base)
                self.assertFalse((base / 'run/calls.json').exists())


if __name__ == '__main__':
    unittest.main()
