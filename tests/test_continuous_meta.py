"""Protocol tests use only repository-authored sources and mocked transport, never generated Python."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch

from scene_rsi.continuous.baselines import generate
from scene_rsi.continuous.tasks import make_task
from scene_rsi.continuous.meta_experiment import audit,initial_source,run_case,eligible,values_state


class ContinuousMetaTest(unittest.TestCase):
    def test_relay_enforces_input_and_output_budget_without_model(self):
        from scripts import algorithm_proposal_relay as relay
        upstream = Mock(status=200)
        upstream.getheader.return_value = 'text/event-stream'
        upstream.read1.return_value = b''
        with TemporaryDirectory() as temp,patch.object(relay.urllib.request,'urlopen',return_value=upstream) as send:
            root = Path(temp)
            auth = root/'auth.json'
            auth.write_text(json.dumps({'tokens':{'access_token':'FAKE','account_id':'FAKE'}}))
            server = relay.serve(root/'socket',auth,root/'audit.json',max_request_bytes=64,max_output_tokens=17)
            try:
                connection = relay.UnixConnection(str(root/'socket'))
                connection.request('POST','/responses',json.dumps({'model':relay.MODEL,'input':'x'*100}))
                response = connection.getresponse()
                self.assertEqual(response.status,403)
                response.read(); connection.close()
                send.assert_not_called()
                connection = relay.UnixConnection(str(root/'socket'))
                connection.request('POST','/responses',json.dumps({'model':relay.MODEL}))
                response = connection.getresponse()
                self.assertEqual(response.status,200)
                response.read(); connection.close()
                body = json.loads(send.call_args.args[0].data)
                self.assertEqual(body['max_output_tokens'],17)
                self.assertEqual(body['tools'],[])
            finally:
                server.shutdown(); server.server_close()

    def test_audit_retains_original_restrictions(self):
        audit(initial_source('repair'))
        audit('import math, random\ndef layout(p):\n import random\n return random.Random(1).random()')
        for source in ('import os\ndef layout(p): return {}',
                       'def layout(p): return open("secret").read()',
                       'import random\ndef layout(p): return random._os.getcwd()',
                       'import scene_rsi.continuous.geometry\ndef layout(p): return {}'):
            with self.assertRaises(ValueError):
                audit(source)

    def test_original_source_size_checked_before_unparse(self):
        with self.assertRaises(ValueError):
            audit('#'+'x'*131072+'\ndef layout(p): return {}')

    def test_trusted_state_machine_transcription_matches_original_search(self):
        class World:
            def evaluate(self,state):
                loss = sum(sum(q['position'])**2 for q in state['objects'])
                return {'optimizer_loss':loss,'failures':[],'quality':{}}
        task,_ = make_task(21,'doorway','public-fixture')
        for method in ('repair','joint'):
            # ONLY the checked-in human-authored adapter is executed here.
            namespace = {}
            exec(initial_source(method),namespace)
            history,memory = [],None
            for index in range(24):
                result = namespace['layout']({'phase':'query','task':task,'seed':12,'query':index,
                                              'history':history,'memory':memory})
                proposed = values_state(task,result['values'])
                history.append({'values':result['values'],'report':World().evaluate(proposed)})
                memory = json.loads(json.dumps(result['memory']))
            index = namespace['layout']({'phase':'final','history':history})['index']
            expected,_ = generate(task,World(),method,12)
            self.assertEqual(expected,values_state(task,history[index]['values']))

    def test_exact_query_budget_and_unqueried_final_rejected(self):
        task,_ = make_task(22,'paired','public-fixture')
        class Process:
            requests = []
            def __init__(self,*a): pass
            def request(self,packet):
                self.requests.append(packet['phase'])
                if packet['phase']=='final': return {'index':24}
                return {'values':{name:[0.,0.,0.] for name in task['supports']},'memory':None}
            def close(self): return {}
        class World:
            def __init__(self,*a): pass
            def evaluate(self,*a,**k): return {'quality':{},'failures':[],'optimizer_loss':0}
            def close(self): pass
        with patch('scene_rsi.continuous.meta_experiment.LayoutProcess',Process),patch('scene_rsi.continuous.meta_experiment.MeshWorld',World):
            result = run_case(Path('/unused'),task,1,Path('/unused'),Path('/unused'))
        self.assertEqual(Process.requests.count('query'),24)
        self.assertEqual(result['queries'],24)
        self.assertFalse(result['result']['success'])
        self.assertIn('already evaluated',result['error'])

    def test_algorithm_cannot_override_fixed_assets_or_drop_movable(self):
        task,_ = make_task(23,'inclined','public-fixture')
        with self.assertRaises(ValueError):
            values_state(task,{'storage':[0,0,0]})
        with self.assertRaises(ValueError):
            values_state(task,{name:[0,0,float('nan')] for name in task['supports']})

    def test_preference_cannot_buy_diversity_or_hard_regression(self):
        base = {'runtime_errors':0,'successes':16,'visibility_mean':1.,'diversity_position_rms_m':.4,
                'diversity_rotation_deg':20.,'valid_pair_count':8,'valid_pair_diversity_m':.4,'preference_error_m':.2}
        improved = dict(base,preference_error_m=.1)
        self.assertTrue(eligible(base,improved))
        for field,value in (('successes',15),('visibility_mean',.98),('diversity_position_rms_m',.35),
                            ('diversity_rotation_deg',15.),('valid_pair_count',7),('valid_pair_diversity_m',.35),('runtime_errors',1)):
            changed = dict(improved,**{field:value})
            self.assertFalse(eligible(base,changed),field)


if __name__=='__main__':
    unittest.main()
