"""Independent task checks; no generated candidate code is imported."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
import tempfile
import json
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_meta_hard as experiment
from scene_rsi.benchmark.meta_tasks import make_case, check, dataset, navigation
from scene_rsi.evaluator.verifier import SceneVerifier
from scene_rsi.schema import SceneSpec


class HardMetaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case, cls.witness = make_case('dev', 1, 0)

    def test_constructive_witnesses_pass_original_and_extended_checks(self):
        for split in ('dev', 'selection', 'test'):
            for case, witness in dataset(split, 1):
                result = check(case, witness.to_dict())
                self.assertTrue(result['success'])
                self.assertTrue(result['physical_valid'])
                self.assertGreaterEqual(result['shortest_actions'], case['packet']['minimum_steps'])

    def test_shortest_witness_routes_match_serpentine_geometry(self):
        expected = {'dev':(22, 26, 35), 'selection':(25, 29, 36), 'test':(23, 30, 39)}
        for split, lengths in expected.items():
            for tier, length in enumerate(lengths):
                case, witness = make_case(split, tier, 0)
                spec = SceneSpec.from_dict(case['packet']['spec'])
                path = navigation(spec, witness, case['packet']['start'], case['packet']['goal'])
                self.assertEqual(len(path), length)

    def test_fixed_object_cannot_be_moved_even_to_another_free_pose(self):
        state = self.witness.to_dict()
        name = self.case['packet']['fixed'][0]['id']
        obj = next(o for o in state['objects'] if o['id'] == name)
        obj['yaw_deg'] = (obj['yaw_deg']+180)%360
        result = check(self.case, state)
        self.assertFalse(result['success'])
        self.assertIn('fixed_object_changed', [f['type'] for f in result['failures']])

    def test_shortest_path_bound_is_independent_of_claimed_trajectory(self):
        case = deepcopy(self.case)
        spec = SceneSpec.from_dict(case['packet']['spec'])
        actual = navigation(spec, self.witness, case['packet']['start'], case['packet']['goal'])
        case['packet']['minimum_steps'] = len(actual)+1
        state = self.witness.to_dict()
        state.update(success=True, score=1, trajectory=['up']*1000, goal=[0, 0])
        result = check(case, state)
        self.assertFalse(result['success'])
        self.assertIn('path_too_short', [f['type'] for f in result['failures']])
        self.assertTrue(SceneVerifier().verify(spec, self.witness)['valid'])

    def test_pose_domains_are_enforced(self):
        case = deepcopy(self.case)
        name = next(iter(case['packet']['domains']))
        actual = next(o for o in self.witness.to_dict()['objects'] if o['id'] == name)
        case['packet']['domains'][name] = [p for p in case['packet']['domains'][name] if p != actual]
        result = check(case, self.witness.to_dict())
        self.assertIn('outside_pose_domain', [f['type'] for f in result['failures']])

    def test_splits_and_anonymized_labels_are_reproducible(self):
        a, witness = make_case('dev', 2, 1)
        b, _ = make_case('dev', 2, 1)
        self.assertEqual(a, b)
        self.assertTrue(all(o.id.startswith('item_') for o in witness.objects))
        profiles = {split:{tuple(sorted((c['packet']['width'], c['packet']['depth'])))
                           for c, _ in dataset(split, 1)} for split in ('dev', 'selection', 'test')}
        self.assertFalse(profiles['dev'] & profiles['selection'])
        self.assertFalse(profiles['dev'] & profiles['test'])
        self.assertFalse(profiles['selection'] & profiles['test'])

    def test_selection_uses_success_before_validity_and_keeps_old_ties(self):
        def candidate(index, success, valid):
            return {'index':index, 'selection':{'metrics':{'solvable':success,'valid':valid}}}
        old = candidate(0, 5, 5)
        invalid = candidate(1, 4, 12)
        tie = candidate(2, 5, 5)
        self.assertIs(experiment.selection([old, invalid, tie]), old)

    def test_extension_requires_complete_ceiling_across_all_trials(self):
        def trial(rate):
            return {'candidates':[{'selection':{'metrics':{'solvable':round(rate*12),'valid':12,
                                                         'solvable_rate':rate}}}]}
        self.assertTrue(experiment.saturated([trial(1), trial(1)]))
        self.assertFalse(experiment.saturated([trial(1), trial(.5)]))

    def test_feedback_exports_no_reference_or_heldout_fields(self):
        row = {'success':False,'valid':True,'failures':[{'type':'path_too_short'}],
               'shortest_actions':5,'scene':{},'witness':'private','selection':'private','test':'private'}
        feedback = experiment.feedback([{'packet':{'public':1}}], [row])[0]
        self.assertNotIn('private', str(feedback))
        self.assertEqual(feedback['error'], 'path_too_short')

    def test_failed_proposal_replays_without_model_or_missing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root/'archive'
            run = root/'replay'
            archive.mkdir(); run.mkdir()
            (archive/'calls.json').write_text(json.dumps([{'slot':0, 'status':'generation_failed', 'error':'budget'}]))
            with patch('run_meta_hard.propose', side_effect=AssertionError('live model in replay')):
                result = experiment.fetch({'slot':0}, run, 'test', archive)
            self.assertEqual(result, {'error':'budget'})


if __name__ == '__main__':
    unittest.main()
