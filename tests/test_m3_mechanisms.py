"""Offline ablation boundaries; strategy source never executes on the host."""
import ast
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import check_m3_mechanisms as mechanism


def function(source, name):
    tree = ast.parse(source)
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.dump(node, include_attributes=False)


class M3MechanismTests(unittest.TestCase):
    def test_variants_change_only_their_targeted_execution_path(self):
        source = mechanism.SOURCE.read_text()
        variants = {name: mechanism.ablate(source, name) for name in mechanism.MECHANISMS}
        for name, variant in variants.items():
            self.assertEqual(function(source, '_legacy_layout'), function(variant, '_legacy_layout'))
            if name != 'constraint_margins':
                self.assertEqual(function(source, '_current_diagnosis'), function(variant, '_current_diagnosis'))
            if name != 'probe_delivery':
                self.assertEqual(function(source, '_condition_groups'), function(variant, '_condition_groups'))
        self.assertIn('first = groups[0]', variants['probe_delivery'])
        self.assertIn('_pooled_diagnosis(packet)', variants['history_separation'])
        self.assertNotIn('Frozen joint acceptance requires', variants['constraint_margins'])
        self.assertIn('Treat these as separate measured objectives; a lower optimizer_loss alone is insufficient.',
                      variants['constraint_margins'])
        self.assertEqual(source, mechanism.SOURCE.read_text())

    def test_execution_uses_isolated_process_and_retains_validation_failure(self):
        source = mechanism.SOURCE.read_text()
        packet = mechanism.fixture_packets('position', 'rotation')
        response = {'experiment': 'dense', 'analysis': 'pooled history ablation', 'diagnosis': {
            'objective': 'position', 'status': 'observed', 'probe': 'dense',
            'evidence': ['summary.history'], 'expected_observation': 'fixture'}}
        process = Mock()
        process.request.return_value = response
        process.close.return_value = {'exit_code': 0, 'wall_seconds': .1}
        with TemporaryDirectory() as directory, patch.object(mechanism, 'LayoutProcess', return_value=process) as factory:
            out = Path(directory) / 'decision'
            result, error = mechanism.execute(source, packet, out, Path(directory))
            factory.assert_called_once_with(out / 'payload', Path(directory))
            process.request.assert_called_once_with(packet)
            process.close.assert_called_once()
            self.assertEqual(result, response)
            self.assertIn('not history', error)
            self.assertEqual(mechanism.read(out / 'response.json'), response)
            self.assertFalse(mechanism.read(out / 'validation.json')['passed'])
            self.assertEqual(mechanism.read(out / 'execution.json')['exit_code'], 0)

    def test_wording_or_reordering_does_not_count_as_behavior(self):
        diagnosis = {'objective': 'preference', 'status': 'hypothesis', 'evidence': ['current'],
                     'expected_observation': 'first description'}
        before = {'choose_diagnosis': diagnosis, 'propose_diagnosis': diagnosis,
                  'probe': 'random', 'delivered': [[0, 0], [2, 0]], 'prompt': 'short', 'analysis': 'short'}
        after = deepcopy(before)
        after['prompt'] = 'more words'
        after['choose_diagnosis']['expected_observation'] = 'another description'
        result = mechanism.compare(before, after)
        self.assertFalse(result['nontext_behavior_changed'])
        self.assertTrue(result['prompt_changed'])
        after['delivered'] = [[0, 0], [3, 0]]
        self.assertTrue(mechanism.compare(before, after)['nontext_behavior_changed'])

    def test_feedback_is_the_policy_selected_membership_without_controller_repair(self):
        examples = mechanism.synthetic_examples()
        diagnosis = {'objective': 'preference', 'status': 'hypothesis'}
        choice = {'experiment': 'random', 'diagnosis': diagnosis}
        decision = {'analysis': 'fixture', 'prompt': 'fixture', 'example_indices': list(range(8)),
                    'diagnosis': diagnosis}
        with TemporaryDirectory() as directory, patch.object(mechanism, 'execute', side_effect=[(choice, None), (decision, None)]):
            out = Path(directory)
            result = mechanism.exercise('source', {}, examples, 'generator', out, out)
            task = mechanism.read(out / 'coding_task.json')
            self.assertEqual(result['delivered'], [[i, r] for i in (0, 1) for r in range(4)])
            self.assertEqual({row['condition_index'] for row in task['feedback']['examples']}, {0, 1})
            self.assertEqual(task['feedback']['constraints'], mechanism.CONSTRAINTS)
            self.assertEqual(len(task['feedback']['examples']), 8)
            self.assertEqual(result['coding_model_calls'], 0)

    def test_development_loader_never_opens_selection_or_test_records(self):
        accessed = []
        cases = [{'task': {'private_dev': True}}, {'task': {'private_dev': True}}]
        def read(path):
            accessed.append(path)
            if path.name == 'private_tasks.json':
                return {'dev': cases, 'selection': 'must not inspect', 'test': 'must not inspect'}
            return {'dev_record': True}
        public = [{'condition_index': i} for i in range(2)]
        with patch.object(mechanism, 'read', side_effect=read), \
             patch.object(mechanism, 'examples4', return_value=public), \
             patch.object(mechanism, 'summary4', return_value={'tasks': 2}), \
             patch.object(mechanism, 'history', return_value={}), \
             patch.object(mechanism, 'meta_summary', return_value={}):
            summary, examples = mechanism.load_development(Path('/development'))
        self.assertEqual(examples, public)
        self.assertEqual(summary['current']['baseline']['tasks'], 2)
        self.assertEqual(len(accessed), 9)
        self.assertTrue(all('/baseline/dev/' in str(path) for path in accessed[1:]))


if __name__ == '__main__':
    unittest.main()
