import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from run_spatial_ablation import ARMS, CONTRACT, MAX_CALLS, ceiling, pools


class SpatialAblationTest(unittest.TestCase):
    def test_only_probe_policy_is_swapped(self):
        self.assertEqual(ARMS, {'M0':('M0','M0'), 'M0_M1probe':('M0','M1'),
                               'M1_random':('M1','M0'), 'M1':('M1','M1')})
        self.assertEqual(MAX_CALLS, 16)

    def test_disjoint_pools_equal_probe_cost(self):
        common, probes, selected, final = pools()
        self.assertEqual(len(common), 6)
        seen = {c['id'] for c in common}
        for probe in probes.values():
            self.assertEqual(len(probe), 6)
            self.assertFalse(seen & {c['id'] for c in probe})
            seen.update(c['id'] for c in probe)
        self.assertFalse(seen & {c['id'] for c in selected+final})
        self.assertEqual((len(selected), len(final)), (18,27))
        self.assertFalse({c['id'] for c in selected} & {c['id'] for c in final})

    def test_ceiling_requires_every_trial(self):
        def trial(n):
            return {'candidates':[{'selection':{'metrics':{'solvable':n, 'valid':18, 'solvable_rate':n/18}}}]}
        self.assertFalse(ceiling([trial(18),trial(16)]))
        self.assertTrue(ceiling([trial(18),trial(17)]))

    def test_prompt_has_contract_and_no_hidden_analysis(self):
        self.assertIn('elevations', CONTRACT)
        self.assertIn('5 CPU seconds', CONTRACT)
        self.assertNotIn('search_baseline', CONTRACT)
        self.assertNotIn('witness_poses', CONTRACT)


if __name__ == '__main__':
    unittest.main()
