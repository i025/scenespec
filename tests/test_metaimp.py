"""Offline checks of the immutable controller/mutable-policy boundary."""
import importlib.util
from pathlib import Path
import sys
import json
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
import run_metaimp as experiment


class MetaImpTests(unittest.TestCase):
    def test_policy_cannot_change_budget_or_evaluator(self):
        for result in ({'experiment':'random', 'analysis':'', 'budget':999},
                       {'experiment':'new_suite', 'analysis':''}):
            with self.assertRaises(ValueError):
                experiment.validate_decision(result, {'phase':'choose'})
        with self.assertRaises(ValueError):
            experiment.validate_decision({'analysis':'', 'prompt':'', 'example_indices':[], 'score':1},
                                         {'phase':'propose', 'examples':[]})

    def test_private_or_invalid_example_indices_refused(self):
        for indices in ([True], [-1], [1], [0, 0]):
            with self.assertRaises(ValueError):
                experiment.validate_decision({'analysis':'', 'prompt':'', 'example_indices':indices},
                                             {'phase':'propose', 'examples':[{}]})

    def test_valid_policy_data_and_bounded_text(self):
        experiment.validate_decision({'experiment':'dense', 'analysis':'diagnose dev only'}, {'phase':'choose'})
        with self.assertRaises(ValueError):
            experiment.validate_decision({'experiment':'dense', 'analysis':'x'*3001}, {'phase':'choose'})

    def test_invalid_candidate_cannot_win_on_diagnostic_score(self):
        initial = {'solvable':5, 'solvable_rate':5/24}
        invalid = {'tasks':24, 'valid':23, 'solvable':23, 'physics_replays':23, 'solvable_rate':23/24}
        dev = {'tasks':24, 'valid':24, 'solvable':24, 'physics_replays':24}
        self.assertEqual(experiment.selected_score(initial, invalid, dev), (5/24, False))
        valid = dict(invalid, valid=24)
        self.assertEqual(experiment.selected_score(initial, valid, dev), (23/24, True))
        self.assertEqual(experiment.selected_score(initial, valid, dict(dev, physics_replays=23)), (5/24, False))

    def test_call_quota_checked_before_model_access(self):
        controller = object.__new__(experiment.Experiment)
        controller.calls = [{}]*6
        with self.assertRaisesRegex(ValueError, 'quota'):
            controller.call('seventh', {}, '', '', '')

    def test_feedback_projection_does_not_export_private_fields(self):
        row = {'packet':{'width':4}, 'valid':True, 'solvability':'solvable',
               'reference_steps':7, 'reference_actions':['right'], 'val_task':'private',
               'feasibility':{'witness':'private'}, 'controller_log':'private'}
        public = experiment.examples([row])[0]
        self.assertEqual(set(public), {'packet', 'solvable', 'valid', 'error', 'shortest_actions'})
        self.assertNotIn('private', str(public))

    def test_offline_replay_never_invokes_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archived = root/'archive/c0/output'
            archived.mkdir(parents=True)
            source = 'def layout(p): return p\n'
            (archived/'proposal.json').write_text(json.dumps({'code':source, 'rationale':'fixture'}))
            controller = object.__new__(experiment.Experiment)
            controller.run = root/'replay'
            controller.run.mkdir()
            controller.replay = root/'archive'
            controller.parent = 'test'
            controller.calls = []
            with patch('run_metaimp.propose', side_effect=AssertionError('live model in replay')):
                code, record = controller.call('fixture', {}, '', '', 'scene_rsi/agent/meta_policy.py')
            self.assertEqual(code, source)
            self.assertEqual(record['mode'], 'offline_replay')


if __name__ == '__main__':
    unittest.main()
