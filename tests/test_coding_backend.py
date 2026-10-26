"""No actual inference: neutral patch API, fixed strategies and transport caps."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scene_rsi.meta.coding import improvement_task,materialize,tools_only
from scene_rsi.meta import coding
from scripts.run_native_continuous import candidate
from scripts.run_continuous_meta import CONTRACT

class CodingBackendTests(unittest.TestCase):
    def test_native_relay_retains_tools_records_usage_and_enforces_call_cap(self):
        from scripts.algorithm_proposal_relay import UnixConnection
        usage={'input_tokens':11,'output_tokens':3,'total_tokens':14}
        class Response:
            status=200
            def getheader(self,*args):return 'text/event-stream'
            def __iter__(self):
                yield ('data: '+json.dumps({'type':'response.completed','response':{'status':'completed','usage':usage}})+'\n\n').encode()
            def close(self):pass
        with TemporaryDirectory() as tmp,patch.object(coding.urllib.request,'urlopen',return_value=Response()) as send:
            root=Path(tmp);auth=root/'auth.json'
            auth.write_text(json.dumps({'tokens':{'access_token':'fake-secret','account_id':'fake-account'}}))
            budget=dict(coding.BUDGET,model_requests=1,output_tokens_per_request=17)
            server=coding.serve(root/'socket',auth,root/'record.json',budget)
            try:
                for expected in (200,403):
                    connection=UnixConnection(str(root/'socket'))
                    connection.request('POST','/responses',json.dumps({'model':coding.MODEL,'input':[]}))
                    response=connection.getresponse();self.assertEqual(response.status,expected)
                    response.read();connection.close()
            finally:server.shutdown();server.server_close()
            self.assertEqual(send.call_count,1)
            sent=json.loads(send.call_args.args[0].data)
            self.assertEqual(sent['tool_choice'],'auto')
            self.assertEqual(sent['max_output_tokens'],17)
            record=(root/'record.json').read_text()
            self.assertNotIn('fake-secret',record)
            self.assertEqual(json.loads(record)['requests'][0]['usage'],usage)

    def task(self):
        return improvement_task({'meta_analysis':'UNCHANGED analysis','proposal_instruction':'UNCHANGED strategy',
               'dev_metrics':{'solvable_rate':1.},'dev_examples':[],
               'generator_source':'def layout(packet):\n    return {}\n'},CONTRACT)

    def test_strategy_preserved_and_source_is_a_workspace_file(self):
        task=self.task()
        self.assertEqual(task['analysis'],'UNCHANGED analysis')
        self.assertEqual(task['strategy'],'UNCHANGED strategy')
        self.assertEqual(task['allowed_files'],['algorithm.py'])
        self.assertEqual(task['budget']['mesh_queries'],24)
        self.assertNotIn('generator_source',task['feedback'])

    def test_native_response_cap_and_incomplete_reason_are_preserved(self):
        from scripts.algorithm_proposal_relay import UnixConnection
        usage={'input_tokens':31,'output_tokens':5000,'total_tokens':5031}
        class Response:
            status=200
            def getheader(self,*args):return 'text/event-stream'
            def __iter__(self):
                yield ('data: '+json.dumps({'type':'response.incomplete','response':{
                    'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'},'usage':usage}})+'\n\n').encode()
            def close(self):pass
        with TemporaryDirectory() as tmp,patch.object(coding.urllib.request,'urlopen',return_value=Response()) as send:
            root=Path(tmp);auth=root/'auth.json'
            auth.write_text(json.dumps({'tokens':{'access_token':'fake-secret','account_id':'fake-account'}}))
            budget=dict(coding.BUDGET,total_tokens=400000)
            server=coding.serve(root/'socket',auth,root/'record.json',budget)
            connection=UnixConnection(str(root/'socket'))
            try:
                connection.request('POST','/responses',json.dumps({'model':coding.MODEL,'input':[],'max_output_tokens':4096}))
                response=connection.getresponse()
                self.assertEqual(response.status,200)
                self.assertIn(b'response.incomplete',response.read())
            finally:
                connection.close();server.shutdown();server.server_close()
            sent=json.loads(send.call_args.args[0].data)
            self.assertEqual(sent['max_output_tokens'],16384)
            record=json.loads((root/'record.json').read_text())['requests'][0]
            self.assertEqual(record['response_status'],'incomplete')
            self.assertEqual(record['incomplete_details'],{'reason':'max_output_tokens'})
            self.assertEqual(record['usage'],usage)
            self.assertGreaterEqual(record['token_reservation'],16384)

    def test_failed_cli_patch_is_not_an_eligible_candidate(self):
        task=self.task()
        def failed_backend(received,directory):
            return {'status':'failed','patch':'an archived partial patch','summary':'incomplete response'}
        with TemporaryDirectory() as tmp,patch('scripts.run_native_continuous.materialize') as consume:
            result,source=candidate(task,Path(tmp),failed_backend)
        self.assertEqual(result['status'],'failed')
        self.assertIsNone(source)
        consume.assert_not_called()

    def test_api_style_backend_needs_no_codex_events(self):
        task=self.task()
        patch='--- a/algorithm.py\n+++ b/algorithm.py\n@@ -1,2 +1,2 @@\n def layout(packet):\n-    return {}\n+    return {"ok": True}\n'
        def api_backend(received,directory):
            self.assertEqual(received['strategy'],task['strategy'])
            return {'status':'completed','patch':patch,'summary':'API patch, no CLI files'}
        with TemporaryDirectory() as tmp:
            result,source=candidate(task,Path(tmp),api_backend)
        self.assertIn('"ok": True',source)
        self.assertEqual(result['summary'],'API patch, no CLI files')

    def test_patch_cannot_modify_other_files(self):
        patch='--- a/CONTRACT.md\n+++ b/CONTRACT.md\n@@ -1 +1 @@\n-old\n+new\n'
        with TemporaryDirectory() as tmp,self.assertRaises(ValueError):
            materialize('def layout(p): return {}\n',patch,Path(tmp)/'target')

    def test_native_bootstrap_does_not_advertise_extra_agents(self):
        body={'input':[{'type':'additional_tools','tools':[{'name':'functions','tools':[{'name':'exec'},{'name':'request_user_input'}]},
                                                          {'name':'collaboration','tools':[{'name':'spawn_agent'}]}]},
                       {'type':'message','role':'developer','content':[{'text':'<multi_agent_role> extra agent instructions'}]},
                       {'type':'message','role':'user','content':[{'text':'unchanged research instructions'}]}]}
        filtered=tools_only(copy.deepcopy(body))
        self.assertEqual([t['name'] for t in filtered['input'][0]['tools']],['functions'])
        self.assertEqual([t['name'] for t in filtered['input'][0]['tools'][0]['tools']],['exec'])
        self.assertEqual(len(filtered['input']),2)
        self.assertEqual(filtered['input'][1],body['input'][2])
        self.assertFalse(filtered['parallel_tool_calls'])

if __name__=='__main__':unittest.main()
