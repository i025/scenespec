"""Offline tests for shared token caps and the four-proposal/test barrier."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scene_rsi.meta import coding
from scripts.algorithm_proposal_relay import UnixConnection
from scripts import run_paired_repair as experiment


class PairedRepairTests(unittest.TestCase):
    def quota(self,usage):
        class Response:
            status=200
            def getheader(self,*args):return 'text/event-stream'
            def __iter__(self):
                response={'status':'completed'}
                if usage is not None:response['usage']=usage
                yield ('data: '+json.dumps({'type':'response.completed','response':response})+'\n\n').encode()
            def close(self):pass
        with TemporaryDirectory() as tmp,patch.object(coding.urllib.request,'urlopen',return_value=Response()) as send:
            root=Path(tmp);auth=root/'auth.json';coding.save(auth,{'tokens':{'access_token':'fake','account_id':'fake'}})
            server=coding.serve(root/'socket',auth,root/'audit.json',dict(coding.BUDGET,total_tokens=25000))
            try:
                for expected in (200,403):
                    connection=UnixConnection(str(root/'socket'))
                    connection.request('POST','/responses',json.dumps({'model':coding.MODEL,'input':[]}))
                    response=connection.getresponse();self.assertEqual(response.status,expected)
                    response.read();connection.close()
            finally:server.shutdown();server.server_close()
            self.assertEqual(send.call_count,1)
            audit=json.loads((root/'audit.json').read_text())
            self.assertTrue(audit['token_quota_rejected'])
            self.assertGreater(audit['requests'][0]['token_reservation'],4096)

    def test_actual_spend_prevents_another_reserved_call(self):
        self.quota({'input_tokens':23000,'output_tokens':1000,'total_tokens':24000})

    def test_unknown_usage_prevents_another_call(self):self.quota(None)

    def test_four_proposals_close_before_test_and_cannot_restart(self):
        metrics={'runtime_errors':0,'successes':16,'visibility_mean':1.,'diversity_position_rms_m':1.,
                 'diversity_rotation_deg':90.,'valid_pair_count':8,'valid_pair_diversity_m':1.,'preference_error_m':.3}
        proposed=[];phases=[]
        def generate(task,folder,backend):
            proposed.append(task)
            return {'status':'completed','usage':{'total_tokens':12}},'def layout(p): return {}\n'
        def evaluate(source,cases,folder,*args):
            if folder.name=='test':self.assertEqual(len(proposed),4)
            phases.append(folder.name)
            return dict(metrics,preference_error_m=.1),[]
        with TemporaryDirectory() as tmp:
            root=Path(tmp);jobs=[]
            for pair in range(2):
                base=root/f'pair{pair}';base.mkdir();coding.save(base/'private_tasks.json',{'dev':[],'selection':[],'test':[]})
                summary=base/'baseline_repair/selection';summary.mkdir(parents=True);coding.save(summary/'summary.json',metrics)
                for slot,policy in enumerate(('M0','M1')):
                    folder=base/f'c{slot}';folder.mkdir();coding.save(folder/'task.json',{'dev_only':True,'pair':pair})
                    jobs.append({'pair':pair,'slot':slot,'policy':policy,'K':1,'status':'reserved'})
            coding.save(root/'calls.json',jobs)
            with patch.object(experiment,'candidate',side_effect=generate),patch.object(experiment,'evaluate',side_effect=evaluate),patch.object(experiment,'git',return_value='offline'),patch.object(experiment,'load_rows',return_value=[]),patch.object(experiment,'bootstrap_difference',return_value={'mean':0.}):
                experiment.execute(root,root,None)
                with self.assertRaises(RuntimeError):experiment.execute(root,root,None)
            self.assertEqual(len(proposed),4)
            self.assertTrue((root/'proposals_complete.json').exists())
            self.assertEqual(phases.count('test'),6)
            self.assertEqual(phases[:8],['dev','selection']*4)


if __name__=='__main__':unittest.main()
