"""No inference or host policy execution: boundaries and four-seed statistics."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import run_evidence_m3 as experiment


class EvidenceM3Tests(unittest.TestCase):
    def test_old_policy_schema_is_retained_but_not_qualified_as_m3(self):
        packet = experiment.fixture_packets('position', 'none')
        result = {'experiment': 'dense', 'analysis': 'old frozen policy'}
        experiment.check_diagnosis(result, packet, False)
        with self.assertRaises(ValueError):
            experiment.check_diagnosis(result, packet, True)

    def test_observed_diagnosis_cannot_cite_history_as_current_fact(self):
        packet = experiment.fixture_packets('position', 'rotation')
        result = {'experiment': 'dense', 'analysis': 'fixture', 'diagnosis': {
            'objective': 'position', 'status': 'observed', 'probe': 'dense',
            'expected_observation': 'compare positions',
            'evidence': ['summary.history.rotation_retention_mean']}}
        with self.assertRaises(ValueError): experiment.check_diagnosis(result, packet, True)
        result['diagnosis']['evidence'] = ['summary.current.baseline.diversity_position_rms_m',
                                          'summary.current.candidate.diversity_position_rms_m']
        experiment.check_diagnosis(result, packet, True)
        result['diagnosis']['evidence'] = ['summary.current.missing']
        with self.assertRaises(KeyError): experiment.check_diagnosis(result, packet, True)

    def test_controller_must_not_repair_missing_or_incomplete_probe(self):
        examples = [{'condition_index': i, 'repeat': r} for i in range(4) for r in range(4)]
        probe = {'condition_indices': [2, 3]}
        with self.assertRaises(ValueError): experiment.check_delivery(examples, examples[:8], probe, True)
        with self.assertRaises(ValueError): experiment.check_delivery(examples, examples[8:11], probe, True)
        delivered = list(reversed(examples[:4] + examples[8:12]))
        result = experiment.check_delivery(examples, delivered, probe, True)
        self.assertEqual(result['complete_probe_conditions'], [2])
        self.assertEqual(result['controller_added_examples'], 0)
        self.assertEqual(result['delivered'], experiment.delivered_pairs(delivered))
        # Frozen M2 is measured as-is; it does not silently receive new examples.
        old = experiment.check_delivery(examples, examples[:8], probe, False)
        self.assertEqual(old['delivered'], experiment.delivered_pairs(examples[:8]))

    def test_four_seed_pair_counts_and_distances_preserve_original_definition(self):
        case = experiment.read(experiment.OLD / 'dev_cases.json')[0]
        original = [experiment.read(experiment.OLD / 'c2/dev' / f'00_{i}.json') for i in range(2)]
        rows = []
        for i in range(4):
            row = deepcopy(original[i % 2]); row.update(task_index=0, repeat=i); rows.append(row)
        summary = experiment.summary4([case], rows)
        expected = experiment.distance(case['task'], original[0]['state'], original[1]['state'])
        self.assertEqual(summary['runs'], 4)
        self.assertEqual(summary['successes'], 4)
        self.assertEqual(summary['valid_pair_count'], 6)
        self.assertAlmostEqual(summary['diversity_position_rms_m'], expected['position_rms_m'] * 4/6)
        self.assertAlmostEqual(summary['diversity_rotation_deg'], expected['rotation_mean_deg'] * 4/6)
        self.assertAlmostEqual(summary['valid_pair_diversity_m'], summary['diversity_position_rms_m'])

    def test_failed_qualification_blocks_new_conditions(self):
        with TemporaryDirectory() as directory, patch.object(experiment, 'read', return_value={'passed': False}), patch.object(experiment, 'conditions') as conditions:
            with self.assertRaises(RuntimeError): experiment.prepare(Path(directory), Path('/unused'))
            conditions.assert_not_called()

    def test_strategy_edit_cannot_restart_or_spend_another_slot(self):
        with TemporaryDirectory() as directory:
            backend = unittest.mock.Mock()
            with self.assertRaises(FileExistsError): experiment.create(Path(directory), backend)
            backend.assert_not_called()


if __name__ == '__main__':
    unittest.main()
