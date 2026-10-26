"""Offline analysis checks. Generated algorithms are never executed on the host."""
import ast
import unittest

from scripts.ablate_m2 import OLD, VARIANTS, final_selector, paired_difference, variant_source
from scripts.audit_m2_paths import evidence
from scene_rsi.continuous.meta_experiment import audit


class M2AttributionTests(unittest.TestCase):
    def test_all_interventions_remain_within_original_static_audit(self):
        original = (OLD / 'c2/source.py').read_text()
        for name in VARIANTS:
            with self.subTest(name=name):
                self.assertTrue(audit(variant_source(name))['passed'])
        self.assertEqual(variant_source('full'), original)
        self.assertEqual((OLD / 'c2/source.py').read_text(), original)

    def test_interventions_never_change_final_selection_or_contract(self):
        def function(source, name):
            return next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
        original = variant_source('full')
        for name in VARIANTS:
            source = variant_source(name)
            self.assertEqual(ast.dump(function(source, 'best_index')), ast.dump(function(original, 'best_index')))
            self.assertEqual(ast.dump(function(source, 'layout').body[1]), ast.dump(function(original, 'layout').body[1]))

    def test_query_valid_selector_does_not_assume_physics_success(self):
        trace = [{'optimizer_loss': .1, 'failures': [{'type': 'mesh_visibility'}]},
                 {'optimizer_loss': .3, 'failures': []},
                 {'optimizer_loss': .2, 'failures': []}]
        self.assertEqual(final_selector(trace, 'first_query_valid'), 1)
        self.assertEqual(final_selector(trace, 'last_query_valid'), 2)
        for row in trace:
            row['failures'] = [{'type': 'support_contact'}]
        self.assertEqual(final_selector(trace, 'last_query_valid'), 0)

    def test_paired_intervals_use_tasks_not_individual_runs(self):
        before = [{'task_index': i, 'position_m': .5} for i in range(8)]
        after = [{'task_index': i, 'position_m': .4} for i in range(8)]
        result = paired_difference(before, after)['position_m']
        self.assertEqual(result['tasks'], 8)
        self.assertAlmostEqual(result['mean'], -.1)
        for bound in result['task_cluster_bootstrap_95']:
            self.assertAlmostEqual(bound, -.1)

    def test_path_evidence_reconstructs_real_backend_input(self):
        recorded = evidence()
        self.assertTrue(recorded['same_backend_budget'])
        self.assertTrue(recorded['same_final_example_membership'])
        for policy in recorded['policies'].values():
            self.assertTrue(policy['actual_task_analysis_matches_propose'])
            self.assertTrue(policy['probe_pairs_available'])
            self.assertEqual(policy['probe_pairs_delivered'], [])
            self.assertTrue(policy['full_experience_in_backend'])
            self.assertFalse(policy['full_experience_in_policy_summary'])
            self.assertTrue(all(r['exit_code'] == 0 for r in policy['execution'].values()))

    def test_historical_deficits_match_executed_diagnosis(self):
        recorded = evidence()
        m2 = recorded['policies']['M2']
        self.assertEqual(m2['choose_summary']['multiobjective']['success_rate'], 1.)
        self.assertNotIn('position_retention', m2['choose_summary']['multiobjective'])
        for key, deficit in recorded['historical_deficits_not_current_measurements'].items():
            self.assertIn(key + '=' + str(round(deficit, 4)), m2['choice']['analysis'])
        self.assertFalse(recorded['function_changes']['_brief']['changed'])


if __name__ == '__main__':
    unittest.main()
