"""Backend-neutral improvement tasks; a native Codex implementation returns Git patches."""
import http.server
import json
import os
from pathlib import Path
import signal
import socketserver
import subprocess
import threading
import time
import urllib.error
import urllib.request

from .layout_process import container_command

ROOT=Path(__file__).resolve().parents[2]
MODEL='gpt-6.1-sol'
BUDGET={'wall_seconds':600,'model_requests':6,'input_bytes_total':1048576,
        'input_bytes_per_request':196608,'output_tokens_per_request':4096,'mesh_queries':24,
        'editor_cpu_seconds_per_process':45,'editor_virtual_as_gib':16,'editor_tmp_mib':64,
        'editor_shared_uid_task_limit':4096,'candidate_cpu_seconds':5,'candidate_memory_mib':256}


def save(path,value):
    path.write_text(json.dumps(value,indent=2)+'\n')


def improvement_task(request,contract):
    """Adapt the frozen strategy's research instructions, not its decision policy."""
    contract=contract[contract.index("layout(packet) receives"):]
    contract=contract.replace('Return complete source, not an experimental plan.','Edit algorithm.py in place, not an experimental plan.')
    contract=contract.replace('No model calls/tools are available here.','No model calls/tools are available inside candidate execution.')
    return {'objective':'Improve general continuous layout preference error while preserving hard correctness and diversity.',
            'analysis':request['meta_analysis'],'strategy':request['proposal_instruction'],
            'feedback':{'metrics':request['dev_metrics'],'examples':request['dev_examples']},
            'allowed_files':['algorithm.py'],'budget':dict(BUDGET),
            'files':{'algorithm.py':request['generator_source']},'contract':contract}


def git(directory,*args):
    return subprocess.check_output(['git','-c','core.hooksPath=/dev/null','-C',str(directory),*args],text=True).strip()


def tools_only(body):
    """CLI-specific transport policy; nothing here enters the meta-policy API."""
    inputs=[]
    for item in body.get('input',[]):
        if item.get('type')=='additional_tools':
            item['tools']=[t for t in item['tools'] if t.get('name')=='functions']
            for namespace in item['tools']:
                namespace['tools']=[t for t in namespace['tools'] if t.get('name') in ('exec','wait')]
        texts=' '.join(c.get('text','') for c in item.get('content',[]) if isinstance(c,dict))
        if item.get('role')=='developer' and ('<multi_agent_role>' in texts or '<multi_agent_mode>' in texts):
            continue
        inputs.append(item)
    body['input']=inputs
    body.update(tool_choice='auto',parallel_tool_calls=False,store=False,stream=True)
    return body


def serve(socket_path,auth_path,audit,budget):
    auth=json.loads(auth_path.read_text())['tokens']
    record={'model':MODEL,'requests':[],'input_bytes_total':0}
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            size=int(self.headers.get('Content-Length','0'))
            if self.path!='/responses' or not 0<size<=budget['input_bytes_per_request'] or len(record['requests'])>=budget['model_requests']:
                self.send_error(403,'coding request quota');return
            body=tools_only(json.loads(self.rfile.read(size)))
            if body.get('model')!=MODEL:
                self.send_error(403,'fixed model');return
            body['max_output_tokens']=budget['output_tokens_per_request']
            encoded=json.dumps(body).encode()
            if record['input_bytes_total']+len(encoded)>budget['input_bytes_total']:
                self.send_error(403,'coding input budget');return
            row={'index':len(record['requests']),'request_bytes':len(encoded),'status':'reserved','usage':None}
            record['requests'].append(row);record['input_bytes_total']+=len(encoded);save(audit,record)
            started=time.perf_counter()
            try:
                request=urllib.request.Request('https://chatgpt.com/backend-api/codex/responses',encoded,
                    {'Content-Type':'application/json','Authorization':'Bearer '+auth['access_token'],
                     'ChatGPT-Account-ID':auth['account_id'],'OpenAI-Beta':'responses=experimental',
                     'originator':'codex_cli_rs','User-Agent':'codex_cli_rs/0.163.0'})
                try: response=urllib.request.urlopen(request,timeout=240)
                except urllib.error.HTTPError as error: response=error
                row['http_status']=response.status
                self.send_response(response.status)
                self.send_header('Content-Type',response.getheader('Content-Type','text/event-stream'));self.end_headers()
                output_bytes=0
                for line in response:
                    output_bytes+=len(line)
                    if output_bytes>2*1024**2: raise ValueError('model stream exceeds limit')
                    if line.startswith(b'data: {'):
                        event=json.loads(line[6:]);item=event.get('item',{})
                        if item.get('namespace')=='collaboration' or item.get('name','').startswith('collaboration.'):
                            raise ValueError('extra agents forbidden')
                        details=event.get('response',{})
                        if details.get('usage') is not None: row['usage']=details['usage']
                        if details.get('model'): row['returned_model']=details['model']
                        if details.get('status'): row['response_status']=details['status']
                    self.wfile.write(line);self.wfile.flush()
                response.close();row['status']='completed' if response.status==200 else 'http_error'
            except Exception as error:
                row.update(status='failed',error=type(error).__name__)
            finally:
                row['wall_seconds']=time.perf_counter()-started;save(audit,record)
    class Server(socketserver.UnixStreamServer): pass
    server=Server(str(socket_path),Handler);os.chmod(socket_path,0o600)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server


def run_codex(task,directory,runtime,guard,*,parent_git,auth_path=Path('/home/xymo/.codex/auth.json'),budget=None,server_factory=serve):
    """Public coding boundary: task in, patch/summary/status/usage out. No JSON source response."""
    budget=dict(task['budget'] if budget is None else budget)
    directory.mkdir(parents=True,exist_ok=False)
    workspace=directory/'workspace';workspace.mkdir()
    if task['allowed_files']!=['algorithm.py'] or set(task['files'])!={'algorithm.py'}:
        raise ValueError('one algorithm file only')
    (workspace/'algorithm.py').write_text(task['files']['algorithm.py'])
    public={k:v for k,v in task.items() if k not in ('files','contract')}
    save(workspace/'TASK.json',public);(workspace/'CONTRACT.md').write_text(task['contract'])
    git(workspace,'init','-q');git(workspace,'add','algorithm.py','TASK.json','CONTRACT.md')
    git(workspace,'-c','user.name=Scene RSI Experiment','-c','user.email=scene-rsi@localhost','commit','-q','-m','Initial dev-only coding task')
    base=git(workspace,'rev-parse','HEAD')
    candidate=directory/'candidate.py';candidate.touch()
    save(directory/'reservation.json',{'parent_git':parent_git,'model':MODEL,'budget':budget,'status':'reserved'})
    cli=Path('/home/xymo/.local/bin/codex').resolve()
    command,env=container_command(runtime,[(cli,'/cli','ro'),(cli.parent/'codex-code-mode-host','/codex-code-mode-host','ro'),
        (guard,'/guard','ro'),(guard,'/bin/bash','ro'),(guard,'/bin/sh','ro'),
        (ROOT/'scripts/native_codex_bridge.py','/bridge.py','ro'),(directory/'model.sock','/model.sock','ro'),
        (workspace,'/seed','ro'),(workspace/'.git','/workspace/.git','ro'),
        (workspace/'TASK.json','/workspace/TASK.json','ro'),(workspace/'CONTRACT.md','/workspace/CONTRACT.md','ro'),
        (candidate,'/candidate.py','rw')])
    command[-1:-1]=['--writable-tmpfs','--scratch','/workspace','--scratch','/state']
    args=['exec','--ignore-user-config','--ignore-rules','--ephemeral','--json','--skip-git-repo-check',
          '--sandbox','danger-full-access','-C','/workspace','--model',MODEL]
    config={'approval_policy':'never','model_provider':'relay','model_reasoning_effort':'medium',
        'model_providers.relay.name':'bounded native editor','model_providers.relay.base_url':'http://127.0.0.1:28731',
        'model_providers.relay.env_key':'OPENAI_API_KEY','model_providers.relay.wire_api':'responses',
        'model_providers.relay.requires_openai_auth':False,'model_providers.relay.supports_websockets':False,
        'model_providers.relay.request_max_retries':0,'model_providers.relay.stream_max_retries':0,
        'features.shell_tool':True,'features.code_mode_host':True,'features.shell_snapshot':False,
        'features.multi_agent':False,'features.apps':False,'features.browser_use':False,'features.plugins':False,'web_search':'disabled'}
    for key,value in config.items(): args+=['-c',key+'='+json.dumps(value)]
    args+=['-']
    prompt='''Read TASK.json, CONTRACT.md and algorithm.py from this independent Git workspace.
Use only native file editing. Modify ONLY algorithm.py. Your final response is a brief modification
summary; do NOT output full source or JSON code. TASK strategy is the frozen meta-agent research
strategy; any old "return replacement source" wording means edit the algorithm file in this backend.
The editor command tool is intentionally a fixed reader: pwd, ls, rg --files, and cat of algorithm.py,
TASK.json or CONTRACT.md. Read them with `cat algorithm.py TASK.json CONTRACT.md` and set the command
tool max_output_tokens=24000 to avoid truncating feedback. Git diffs are captured externally; do not run git.
Use tools.apply_patch for edits. No shell expressions, sed, Python execution or tests here. The trusted
external controller performs syntax and Mesh tests later. Do not spawn agents, use network, request
permissions or inspect other paths. Complete one general algorithm-level improvement within budget.
'''
    server=server_factory(directory/'model.sock',auth_path,directory/'model_calls.json',budget)
    started=time.perf_counter();status='failed'
    try:
        with (directory/'events.jsonl').open('wb') as events,(directory/'cli.stderr').open('wb') as errors:
            process=subprocess.Popen(command+['/usr/local/bin/python3','-I','-B','/bridge.py']+args,
                env=env,stdin=subprocess.PIPE,stdout=events,stderr=errors,start_new_session=True)
            try:
                process.communicate(prompt.encode(),timeout=budget['wall_seconds'])
                status='completed' if process.returncode==0 else 'failed'
            except subprocess.TimeoutExpired:
                status='timed_out';os.killpg(process.pid,signal.SIGKILL);process.wait()
        save(directory/'cli.json',{'exit_code':process.returncode,'status':status,'wall_seconds':time.perf_counter()-started,'argv':args})
    finally:
        server.shutdown();server.server_close();(directory/'model.sock').unlink(missing_ok=True)
    source=candidate.read_text()
    if source: (workspace/'algorithm.py').write_text(source)
    patch=git(workspace,'diff','--no-ext-diff','--','algorithm.py')
    if patch: patch+='\n'
    (directory/'candidate.diff').write_text(patch)
    messages=[]
    for line in (directory/'events.jsonl').read_text().splitlines():
        try: item=json.loads(line).get('item',{})
        except json.JSONDecodeError: continue
        if item.get('type')=='agent_message': messages.append(item.get('text',''))
    summary=messages[-1] if messages else ''
    (directory/'summary.md').write_text(summary+'\n')
    if status=='completed' and not patch: status='empty_patch'
    candidate_git=None
    if patch:
        git(workspace,'add','algorithm.py')
        git(workspace,'-c','user.name=Scene RSI Experiment','-c','user.email=scene-rsi@localhost','commit','-q','-m','Native Codex algorithm edit')
        candidate_git=git(workspace,'rev-parse','HEAD')
    call_file=directory/'model_calls.json'
    rows=json.loads(call_file.read_text())['requests'] if call_file.exists() else []
    usage=None if not rows or any(r.get('usage') is None for r in rows) else {
        key:sum(r['usage'].get(key,0) for r in rows) for key in ('input_tokens','output_tokens','total_tokens')}
    result={'status':status,'patch':patch,'summary':summary,'base_git':base,'candidate_git':candidate_git,
            'wall_seconds':time.perf_counter()-started,'model':MODEL,'model_requests':len(rows),'usage':usage}
    for line in (directory/'cli.stderr').read_text().splitlines():
        if line.startswith('NATIVE_EDITOR_RESOURCES '):
            result['resources']=json.loads(line[len('NATIVE_EDITOR_RESOURCES '):])
    save(directory/'backend_result.json',result)
    return result


def materialize(source,patch,directory):
    """Generic patch consumer, also usable by an API backend with no CLI artifacts."""
    directory.mkdir(parents=True,exist_ok=False)
    (directory/'algorithm.py').write_text(source)
    git(directory,'init','-q')
    args=['git','-c','core.hooksPath=/dev/null','-C',str(directory),'apply']
    listed=subprocess.check_output(args+['--numstat','-'],input=patch.encode()).decode().splitlines()
    if not listed or any(line.split('\t')[-1]!='algorithm.py' for line in listed):
        raise ValueError('patch can modify algorithm.py only')
    subprocess.run(args+['--whitespace=nowarn','-'],input=patch.encode(),check=True,capture_output=True)
    path=directory/'algorithm.py'
    if not path.is_file() or path.is_symlink() or path.stat().st_size>131072:
        raise ValueError('bounded regular algorithm source required')
    return path.read_text()
