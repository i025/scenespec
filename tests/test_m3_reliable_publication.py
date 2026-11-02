"""Archive retries without converting unknown usage to zero or losing raw evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts import summarize_m3_reliable as report


class ReliablePublicationTests(unittest.TestCase):
    def test_credential_pattern_requires_a_token_boundary_not_an_encoded_substring(self):
        token='sk-'+'a'*50
        self.assertTrue(report.CREDENTIAL.search('"token":"'+token+'"'))
        self.assertIsNone(report.CREDENTIAL.search('"encrypted_content":"owDy'+token+'"'))

    def test_http_200_capacity_failure_is_reported_without_reclassifying_retry(self):
        with TemporaryDirectory() as tmp:
            folder=Path(tmp);attempt=folder/'attempt0';attempt.mkdir()
            report.save(folder/'logical.json',{'attempts':[{'directory':'attempt0','status':'failed','capacity_retry_eligible':False}]})
            report.save(attempt/'backend_result.json',{'status':'failed'})
            report.save(attempt/'model_calls.json',{'requests':[{'index':0,'http_status':200,'response_status':'failed','usage':None}]})
            (attempt/'response_0.sse').write_text('data: {"type":"response.failed","response":{"error":{"code":"server_is_overloaded","headers":{"retry-after":"320"}}}}\n')
            (attempt/'events.jsonl').write_text('{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n')
            detail=report.coding_cost(folder)['failed_attempt_details'][0]
            self.assertTrue(detail['capacity_error_reported']);self.assertFalse(detail['retry_eligible_under_frozen_rule'])
            self.assertEqual(detail['requests'][0]['http_status'],200)
            self.assertFalse(detail['requests'][0]['usage_known'])
            self.assertEqual(detail['server_errors'][0]['code'],'server_is_overloaded')

    def test_each_attempt_raw_evidence_and_unknown_cost_survive_export(self):
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir();(source/'policy').mkdir()
            report.save(source/'task.json',{'feedback':'dev only'})
            coding=source/'coding';coding.mkdir()
            ledger={'attempts':[{'directory':'attempt0','status':'failed','capacity_retry_eligible':True},
                                {'directory':'attempt1','status':'completed','capacity_retry_eligible':False}],
                    'actual_total_tokens':None,'known_tokens':50,'unknown_usage_requests':1,'charged_tokens':80050}
            report.save(coding/'logical.json',ledger);report.save(coding/'backend_result.json',{'status':'completed','usage':None})
            for i in range(2):
                folder=coding/f'attempt{i}';folder.mkdir()
                report.save(folder/'backend_result.json',{'status':'failed' if i==0 else 'completed','resources':{'cpu_seconds':i+1}})
                (folder/'response_0.sse').write_text('raw error\n' if i==0 else 'raw completion\n')
                (folder/'events.jsonl').write_text('{"type":"turn.failed"}\n' if i==0 else '{"type":"turn.completed"}\n')
                (folder/'candidate.diff').write_text('' if i==0 else 'actual patch\n')
            dest=Path(tmp)/'public';report.publish_candidate(source,dest)
            cost=report.coding_cost(coding)
            self.assertIsNone(cost['actual_total_tokens']);self.assertEqual(cost['charged_tokens'],80050)
            self.assertEqual(cost['unknown_usage_requests'],1)
            for i in range(2):
                for name in ('response_0.sse','events.jsonl','candidate.diff'):
                    self.assertEqual((coding/f'attempt{i}'/name).read_bytes(),(dest/'coding'/f'attempt{i}'/name).read_bytes())

    def test_statistics_cluster_whole_conditions_and_use_original_gate(self):
        base={'runtime_errors':0,'successes':4,'visibility_mean':.98,'diversity_position_rms_m':.5,
              'diversity_rotation_deg':20.,'valid_pair_count':6,'valid_pair_diversity_m':.5,'preference_error_m':.5}
        candidate=dict(base,preference_error_m=.45)
        result=report.cluster_statistics([base,base],[candidate,candidate],report.np.array([[0,0],[0,1],[1,0],[1,1]]))
        self.assertEqual(result['seeds_kept_together'],4)
        self.assertEqual(result['conditions_per_draw'],2);self.assertEqual(result['resamples'],4)
        self.assertEqual(result['joint_accept_frequency'],1.)
        self.assertAlmostEqual(result['percentile_95']['preference_improvement_m'][0],.05)


if __name__=='__main__':unittest.main()
