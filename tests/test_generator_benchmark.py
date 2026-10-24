"""Regression for baseline measurement, without changing generator or evaluator."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scene_rsi.world import planning, task as tasks
from scene_rsi.world.generator_benchmark import diversity, measure, run, summarize


@unittest.skipUnless(importlib.util.find_spec('pybullet'), 'install requirements-world.txt')
class TestGeneratorMeasurement(unittest.TestCase):
    def test_observation_preserves_original_scenes_and_restores_functions(self):
        original_random, original_solve = tasks.random.Random, planning.solve
        for require in (False, True):
            config = tasks.GeneratorConfig(require_solvable=require)
            for seed in (2, 11, 33):
                expected = tasks.generate(seed, config).to_dict()
                row = measure(seed, config)
                self.assertEqual(row['scene'], expected)
                self.assertEqual(row['sample_attempts'], row['rejected_attempts']+1)
                self.assertEqual(row['rejected_attempts'], sum(row['rejections'].values()))
                if require:
                    self.assertEqual(row['solvability'], 'solvable')
                    self.assertTrue(row['reference_replayed'])
                else:
                    self.assertEqual(row['filter_searches'], 0)
        self.assertIs(tasks.random.Random, original_random)
        self.assertIs(planning.solve, original_solve)

    def test_filtered_and_unfiltered_share_first_rng_proposal_not_final_layout(self):
        config = tasks.GeneratorConfig()
        before, after = measure(0, config), measure(0, replace(config, require_solvable=True))
        self.assertEqual(before['sample_attempts'], 1)
        self.assertEqual(before['solvability'], 'unsolvable')
        self.assertEqual(after['solvability'], 'solvable')
        self.assertGreater(after['rejections']['unsolvable'], 0)
        self.assertNotEqual(before['scene'], after['scene'])

    def test_diversity_detects_duplicate_layouts_and_uses_valid_denominator(self):
        row = measure(11, tasks.GeneratorConfig())
        result = diversity([row, row])
        self.assertEqual(result['unique_layouts'], 1)
        self.assertEqual(result['unique_fraction'], .5)
        self.assertEqual(result['mean_pairwise_obstacle_jaccard_distance'], 0)
        self.assertEqual(result['crate_cell_entropy_bits'], 0)
        self.assertAlmostEqual(result['obstacle_cell_coverage'], 6/36)

    def test_unsolvable_is_distinct_from_invalid_and_agent_insufficient(self):
        raw = measure(0, tasks.GeneratorConfig())
        repaired = measure(2, tasks.GeneratorConfig(require_solvable=True))
        summary = summarize([raw, repaired])
        self.assertEqual(summary['generation_valid_rate'], 1)
        self.assertEqual(summary['solvable_per_request'], .5)
        self.assertEqual(summary['fixed_agent_success_per_request'], 0)
        self.assertEqual(summary['fixed_agent_outcomes'], {'scene_unsolvable': 1, 'agent_insufficient': 1})
        self.assertEqual(summary['physics_reference_successes'], 1)
        self.assertEqual(summary['shortest_action_steps_given_solvable']['count'], 1)

    def test_generation_failure_stays_in_denominator(self):
        with patch.object(tasks, 'generate', side_effect=ValueError('no qualifying scene')):
            failed = measure(0, tasks.GeneratorConfig())
        summary = summarize([failed])
        self.assertEqual(summary['generation_valid_rate'], 0)
        self.assertEqual(summary['solvable_per_request'], 0)
        self.assertEqual(summary['generated'], 0)
        self.assertEqual(summary['shortest_action_steps_given_solvable']['count'], 0)

    def test_dev_feedback_excludes_val_conditions_scenes_and_reference_routes(self):
        with TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            out = Path(temp)/'run'
            report = run(out, samples=1)
            feedback = json.loads((out/'dev_feedback.json').read_text())
            self.assertEqual(len(feedback['groups']), 6)
            self.assertTrue(all(group['split'] == 'dev' for group in feedback['groups']))
            self.assertNotIn('val', feedback)
            self.assertNotIn('"scene":', (out/'dev_feedback.json').read_text())
            self.assertNotIn('reference_actions', (out/'dev_feedback.json').read_text())
            self.assertEqual(report['selection']['model_calls'], 0)
            self.assertIsNone(report['selection']['selected'])
            self.assertIn('dirty', report['controller_git'])

    def test_invalid_sample_count_does_not_create_output(self):
        with TemporaryDirectory() as temp:
            for count in (0, 129, True):
                with self.assertRaises(ValueError):
                    run(Path(temp)/'unused', samples=count)
            self.assertFalse((Path(temp)/'unused').exists())


if __name__ == '__main__':
    unittest.main()
