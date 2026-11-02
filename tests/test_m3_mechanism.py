"""Offline mechanism analysis uses saved inputs and coordinates, not generated code."""
from copy import deepcopy
import unittest

from scripts import analyze_m3_mechanism as analysis


class MechanismAnalysisTests(unittest.TestCase):
    def test_actual_delivered_inputs_have_different_complete_probe_groups(self):
        rows=analysis.inputs()
        self.assertEqual(rows['M2']['probe']['name'],'barriers')
        self.assertEqual(rows['M3']['probe']['name'],'random')
        self.assertEqual([g['dev_condition_index'] for g in rows['M2']['delivered_groups']],[0,5])
        self.assertEqual([g['dev_condition_index'] for g in rows['M3']['delivered_groups']],[1,2])
        self.assertEqual(rows['M3']['propose']['diagnosis']['status'],'hypothesis')
        self.assertEqual(rows['M2']['history'],rows['M3']['history'])

    def test_trace_summary_reconstructs_original_argmin_and_keeps_four_seed_groups(self):
        folder=analysis.PUBLIC/'baseline/dev'
        cases=[analysis.read(analysis.ROOT/'experiments/m3_reliable/results/dev_cases.json')[0]]
        rows=[analysis.read(folder/f'00_{r}.json') for r in range(4)]
        result=analysis.trace_statistics(cases,rows)
        self.assertEqual(result['runs'],4);self.assertEqual(sum(result['final_selected_query_histogram']),4)
        self.assertEqual(result['final_argmin_reconstructed_matches'],4)
        changed=deepcopy(rows);changed[0]['state']['scene_id']='wrong'
        with self.assertRaises(AssertionError):analysis.trace_statistics(cases,changed)

    def test_association_is_condition_level_and_handles_constant_metrics(self):
        base=[{'preference_error_m':1.,'diversity_position_rms_m':.5}]*2
        candidate=[{'preference_error_m':.5,'diversity_position_rms_m':.4}]*2
        result=analysis.condition_association(base,candidate)
        self.assertEqual(result['conditions'],2);self.assertEqual(result['both_conditions'],2)
        self.assertIsNone(result['preference_gain_vs_position_change_pearson'])


if __name__=='__main__':unittest.main()
