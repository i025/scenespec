"""Real CLI with a deterministic LOCAL fake model: no inference or credentials."""
import argparse
import http.server
import json
from pathlib import Path
import socketserver
import sys
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scene_rsi.meta.coding import BUDGET,run_codex,tools_only,save

CODE='''text(await tools.exec_command({cmd:"cat algorithm.py TASK.json CONTRACT.md",login:false,max_output_tokens:1000}));
text(await tools.exec_command({cmd:"cat /home/xymo/.codex/auth.json",login:false}));
text(await tools.exec_command({cmd:"print(123)",shell:"/usr/local/bin/python3",login:false}));
try { text(await tools.apply_patch("*** Begin Patch\\n*** Update File: /workspace/TASK.json\\n@@\\n-no such line\\n+forbidden\\n*** End Patch")); } catch(e) { text(String(e)); }
text(await tools.apply_patch("*** Begin Patch\\n*** Update File: /workspace/algorithm.py\\n@@\\n-    return {}\\n+    return {\\"changed\\": True}\\n*** End Patch"));'''


def fake_server(path,auth,audit,budget,fail_after_edit=False,code=CODE):
    calls=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(data)
            if len(calls)==1:
                item={'type':'custom_tool_call','id':'ct0','call_id':'call0','name':'exec','namespace':'functions','input':code,'status':'completed'}
            else:
                item={'type':'message','id':'m0','role':'assistant','status':'completed',
                      'content':[{'type':'output_text','text':'Offline file edit completed.','annotations':[]}]}
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            events=[ {'type':'response.created','response':{'id':'r'+str(len(calls)),'model':'gpt-6.1-sol','status':'in_progress','output':[]}},
                {'type':'response.output_item.added','output_index':0,'item':item},
                {'type':'response.output_item.done','output_index':0,'item':item},
                {'type':'response.completed','response':{'id':'r'+str(len(calls)),'model':'gpt-6.1-sol','status':'completed','output':[item],
                  'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}}} ]
            if fail_after_edit and len(calls)>1:
                events=[events[0],{'type':'response.incomplete','response':{'id':'r2','model':'gpt-6.1-sol','status':'incomplete',
                         'output':[],'incomplete_details':{'reason':'max_output_tokens'},
                         'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}}}]
            for event in events:
                self.wfile.write(('event: '+event['type']+'\ndata: '+json.dumps(event)+'\n\n').encode());self.wfile.flush()
            save(audit,{'mode':'offline_fake','requests':[]})
            save(audit.parent/'offline_requests.json',calls)
    server=socketserver.UnixStreamServer(str(path),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--guard',type=Path,required=True)
    parser.add_argument('--fail-after-edit',action='store_true',help='offline regression: retain patch but reject failed CLI')
    parser.add_argument('--patch-regressions',action='store_true',help='offline native multi-hunk, sequential and conflicting patch checks')
    args=parser.parse_args()
    source='def layout(packet):\n    return {}\n'
    code=CODE
    if args.patch_regressions:
        source='a = 1\nb = 2\n\n'+source
        patches=[
            ('protected', '*** Begin Patch\n*** Update File: /workspace/TASK.json\n@@\n-  "objective": "offline transport fixture",\n+  "objective": "forbidden",\n*** End Patch'),
            ('duplicate', '*** Begin Patch\n*** Update File: /workspace/algorithm.py\n@@\n-a = 1\n+a = 10\n*** Update File: /workspace/algorithm.py\n@@\n-b = 2\n+b = 20\n*** End Patch'),
            ('multi_hunk', '*** Begin Patch\n*** Update File: /workspace/algorithm.py\n@@\n-a = 1\n+a = 10\n@@\n-b = 2\n+b = 20\n*** End Patch'),
            ('sequential', '*** Begin Patch\n*** Update File: /workspace/algorithm.py\n@@\n-a = 10\n+a = 11\n*** End Patch'),
            ('conflict', '*** Begin Patch\n*** Update File: /workspace/algorithm.py\n@@\n-a = 1\n+a = 999\n*** End Patch')]
        for name,patch in patches:
            code+='\ntry { text({case:'+json.dumps(name)+',result:await tools.apply_patch('+json.dumps(patch)+')}); } catch(e) { text({case:'+json.dumps(name)+',error:String(e)}); }'
            code+='\ntext(await tools.exec_command({cmd:"cat algorithm.py",login:false}));'
    task={'objective':'offline transport fixture','analysis':'not a scored scene candidate','strategy':'edit one return value',
          'feedback':{},'allowed_files':['algorithm.py'],'files':{'algorithm.py':source},
          'contract':'offline fixture','budget':dict(BUDGET,wall_seconds=45)}
    from functools import partial
    result=run_codex(task,args.out/'attempt',args.out/'runtime',args.guard,parent_git='offline',
                     server_factory=partial(fake_server,fail_after_edit=args.fail_after_edit,code=code))
    assert result['status']==('failed' if args.fail_after_edit else 'completed') and result['patch']
    assert '"changed": True' in (args.out/'attempt/candidate.py').read_text()
    calls=json.loads((args.out/'attempt/offline_requests.json').read_text())
    outputs=[item.get('output') for req in calls for item in req.get('input',[]) if item.get('type')=='custom_tool_call_output']
    text=json.dumps(outputs)
    assert 'outside the editor allowlist' in text and 'Editor permits only' in text
    assert 'def layout(packet)' in text
    protected=json.loads((args.out/'attempt/workspace/TASK.json').read_text())
    assert protected['objective']=='offline transport fixture'
    if args.patch_regressions:
        reads=[];cases={}
        for output in outputs:
            for block in output:
                try: value=json.loads(block.get('text',''))
                except json.JSONDecodeError: continue
                if isinstance(value,dict):
                    if 'output' in value: reads.append(value['output'])
                    if 'case' in value: cases[value['case']]=value
        assert 'multiple operations target /workspace/algorithm.py' in cases['duplicate']['error']
        assert 'Failed to find expected lines' in cases['conflict']['error']
        assert 'error' in cases['protected']
        assert any(r.startswith('a = 1\nb = 2\n') for r in reads)
        assert any(r.startswith('a = 10\nb = 20\n') for r in reads)
        assert sum(r.startswith('a = 11\nb = 20\n') for r in reads)==2
        actual=(args.out/'attempt/candidate.py').read_text()
        assert actual=='a = 11\nb = 20\n\ndef layout(packet):\n    return {"changed": True}\n',actual
        save(args.out/'patch_acceptance.json',{'multi_hunk':True,'sequential':True,'conflict_rejected':True,
             'duplicate_rejected_without_partial_write':True,'model_calls':0,
             'fixture_is_not_the_missing_original_failed_patch':True})
    if args.fail_after_edit:
        save(args.out/'acceptance.json',{'failed_cli_patch_preserved':True,'status_remains_failed':True,
             'candidate_git':result['candidate_git'],'model_calls':0,'candidate_execution':False})
        print('offline failed CLI edit retained as a patch, not accepted; model calls 0')
        return
    save(args.out/'acceptance.json',{'native_patch_applied':True,'allowed_read':True,'private_read_denied':True,
        'arbitrary_program_denied':True,'protected_task_unchanged':True,'gpu_absent':True,
        'scratch_limit_bytes':67108864,'model_calls':0,'independent_git_workspace':True,
        'note':'editor is read/patch only; generated layout execution uses the unchanged LayoutProcess'})
    print('native CLI offline read/patch and isolation acceptance passed; model calls 0')


if __name__=='__main__':main()
