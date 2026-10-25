"""Private-container model bridge; the guard exposes read/patch editing only."""
import http.client
import http.server
import os
import socket
import subprocess
import sys
import threading
import shutil
import resource
import json

class Connection(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX)
        self.sock.settimeout(600)
        self.sock.connect('/model.sock')

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        remote=Connection('/model.sock')
        try:
            remote.request('POST',self.path,body,{'Content-Type':'application/json'})
            response=remote.getresponse()
            self.send_response(response.status)
            self.send_header('Content-Type',response.getheader('Content-Type','text/event-stream'))
            self.end_headers()
            while chunk:=response.read1(16384):
                self.wfile.write(chunk);self.wfile.flush()
        finally: remote.close()

for path in ('/state/codex','/state/home'):
    os.makedirs(path,exist_ok=True)
os.environ.update(CODEX_HOME='/state/codex',HOME='/state/home',OPENAI_API_KEY='broker-placeholder',
                  PATH='/:/usr/bin:/bin',TOKIO_WORKER_THREADS='2',RAYON_NUM_THREADS='2')
server=http.server.HTTPServer(('127.0.0.1',28731),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
shutil.copyfile('/seed/algorithm.py','/workspace/algorithm.py')
mounts={p:os.statvfs(p).f_blocks*os.statvfs(p).f_frsize for p in ('/workspace','/state')}
if max(mounts.values())>64*1024**2:
    raise RuntimeError('bounded session filesystem unavailable')
if any(name.startswith(('nvidia','dri')) for name in os.listdir('/dev')):
    raise RuntimeError('GPU device unexpectedly exposed')
for path in ('/workspace/TASK.json','/workspace/CONTRACT.md','/workspace/.git/config'):
    try:
        fd=os.open(path,os.O_WRONLY)
    except OSError:
        pass
    else:
        os.close(fd)
        raise RuntimeError('protected input is writable: '+path)
print('NATIVE_EDITOR_ACCEPTANCE: bounded scratch; protected task/Git; no GPU',file=sys.stderr,flush=True)
try:
    result=subprocess.run(['/guard','--launch','/cli']+sys.argv[1:])
    if result.returncode==0:
        if set(os.listdir('/workspace')) != {'algorithm.py','TASK.json','CONTRACT.md','.git'}:
            raise ValueError('coding workspace changed outside algorithm.py')
        if os.path.islink('/workspace/algorithm.py'):
            raise ValueError('algorithm must be a regular file')
        source=open('/workspace/algorithm.py','rb').read(131073)
        if len(source)>131072: raise ValueError('algorithm exceeds 128KiB')
        with open('/candidate.py','wb') as output: output.write(source)
finally:
    server.shutdown();server.server_close()
    child=resource.getrusage(resource.RUSAGE_CHILDREN)
    own=resource.getrusage(resource.RUSAGE_SELF)
    print('NATIVE_EDITOR_RESOURCES '+json.dumps({'cpu_seconds':child.ru_utime+child.ru_stime+own.ru_utime+own.ru_stime,
          'process_peak_rss_bytes':max(child.ru_maxrss,own.ru_maxrss)*1024,
          'scope':'fresh bridge and waited CLI/helpers; peak is per process, not aggregate RSS'}),file=sys.stderr,flush=True)
raise SystemExit(result.returncode)
