"""Offline cluster sampling, unchanged gates, weighted pairs and unknown service usage."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from scripts import analyze_m3_selection as analysis


def group():
    return dict(runtime_errors=0, successes=4, visibility_mean=.95,
                diversity_position_rms_m=.5, diversity_rotation_deg=20.,
                valid_pair_count=6, valid_pair_diversity_m=.5,
                preference_error_m=1.)


class SelectionAnalysisTests(unittest.TestCase):
    def test_bootstrap_resamples_conditions_not_individual_seeds(self):
        before = [group(), group()]
        after = [dict(before[0], preference_error_m=.97), dict(before[1], preference_error_m=1.02)]
        result = analysis.bootstrap(before, after)
        self.assertEqual(result['conditions_per_draw'], 2)
        self.assertEqual(result['seeds_per_condition_kept_together'], 4)
        self.assertEqual(result['resamples'], 4)
        self.assertEqual(result['accept_count'], 1)
        self.assertEqual(result['accept_frequency'], .25)
        self.assertEqual(result['failure_frequency']['preference'], .75)
        self.assertEqual(result['sole_blocker_frequency']['preference'], .75)
        self.assertEqual(result['diagnostic_acceptance_ignoring_rule']['preference'], 1.)

    def test_valid_pair_means_keep_their_actual_denominators(self):
        groups = [dict(group(), valid_pair_diversity_m=.2),
                  dict(group(), valid_pair_count=3, valid_pair_diversity_m=.8)]
        result = analysis.aggregate(groups, np.array([[0, 1], [1, 1]]))
        np.testing.assert_allclose(result['valid_pair_diversity_m'], [.4, .8])
        np.testing.assert_array_equal(result['valid_pair_count'], [9, 6])
        np.testing.assert_array_equal(result['successes'], [8, 8])

    def test_near_threshold_does_not_change_the_original_acceptance(self):
        before = group()
        for gain in (.009665, .003710):
            result = analysis.split_comparison(before, dict(before, preference_error_m=1. - gain))
            self.assertFalse(result['accepted'])
            self.assertEqual(result['rejected_by'], ['preference'])
            self.assertAlmostEqual(result['checks']['preference']['margin'], gain - .01)
        result = analysis.split_comparison(before, dict(before, preference_error_m=.98,
                                                       diversity_position_rms_m=.449,
                                                       valid_pair_diversity_m=.449))
        self.assertEqual(set(result['rejected_by']), {'position', 'valid_position'})
        self.assertTrue(result['only_position_distribution_blocked'])

    def test_family_decomposition_is_an_identity_not_a_new_selection(self):
        a = {'a': {'conditions': 2, 'preference_improvement_m': .1},
             'b': {'conditions': 1, 'preference_improvement_m': .4}}
        b = {'a': {'conditions': 1, 'preference_improvement_m': .2},
             'b': {'conditions': 1, 'preference_improvement_m': .5}}
        result = analysis.family_shift(a, b)
        self.assertAlmostEqual(result['test_minus_selection_gain_composition_component_m'] +
                               result['test_minus_selection_gain_within_family_component_m'], .35 - .2)

    def test_service_capacity_is_not_zero_cost_or_an_exact_server_time(self):
        with TemporaryDirectory() as directory:
            run = Path(directory)
            calls = [dict(initial='fixture', policy=p, repeat=0) for p in ('M2', 'M3')]
            for call in calls:
                folder = run/'fixture'/call['policy']/'r0/coding'
                folder.mkdir(parents=True)
                failed = call['policy'] == 'M3'
                usage = None if failed else {'total_tokens': 10}
                analysis.save(folder/'backend_result.json', {'status': 'failed' if failed else 'completed',
                              'usage': usage, 'wall_seconds': 3.})
                analysis.save(folder/'model_calls.json', {'requests': [dict(index=0, usage=usage,
                              http_status=429 if failed else 200, status='http_error' if failed else 'completed', wall_seconds=1.)]})
                analysis.save(folder/'cli.json', {'argv': ['request_max_retries=0', 'stream_max_retries=0']})
                (folder/'events.jsonl').write_text('{"type":"turn.failed"}\n' if failed else '{"type":"turn.completed"}\n')
                if failed:
                    analysis.save(folder/'response_0.sse', {'error': {'type': 'rate_limit_error',
                                  'code': 'turn_rate_limit_exceeded', 'message': 'Codex is at capacity, please try again later'}})
                else:
                    (folder/'response_0.sse').write_text('data: {"type":"response.created","response":{"created_at":1234}}\n')
            result = analysis.service_analysis(run, calls)
            self.assertEqual(result['costs']['M3']['unknown_usage_requests'], 1)
            self.assertIsNone(result['costs']['M3']['exact_total_tokens'])
            self.assertEqual(result['costs']['M2']['exact_total_tokens'], 10)
            self.assertIsNone(result['capacity_failures'][0]['response_created_at_utc_if_reported'])
            self.assertIn('approximate', result['timing_caveat'])
            self.assertFalse(result['failed_slots_retried'])


if __name__ == '__main__':
    unittest.main()
