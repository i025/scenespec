"""Experiment-only model relay. Credentials stay in the host controller.

bridge runs inside private Apptainer networking; launch prevents CLI child
processes while allowing Rust threads. No candidate ever mounts this socket.
"""
import ctypes
import errno
import http.client
import http.server
import json
import os
from pathlib import Path
import resource
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time

MODEL = 'gpt-6.1-sol'  # Existing local Codex configuration, fixed for all attempts.


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(150)
        self.sock.connect(self.host)


def serve(socket_path, auth_path, audit):
    """One upstream inference per reserved attempt; never log authentication."""
    auth = json.loads(Path(auth_path).read_text())['tokens']
    record = {'model': MODEL, 'upstream_calls': 0, 'usage': None, 'status': 'not_called'}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            if self.path != '/responses' or not 0 < size <= 524288 or record['upstream_calls']:
                self.send_error(403)
                return
            body = json.loads(self.rfile.read(size))
            if body.get('model') != MODEL:
                self.send_error(403)
                return
            # The model is a proposal-only completion. Local tools are not offered.
            body.update(tools=[], tool_choice='none', store=False, stream=True, max_output_tokens=8192)
            record.update(upstream_calls=1, status='started', request_bytes=size)
            Path(audit).write_text(json.dumps(record, indent=2)+'\n')
            started = time.perf_counter()
            try:
                remote = http.client.HTTPSConnection('chatgpt.com', timeout=150, context=ssl.create_default_context())
                headers = {'Content-Type':'application/json', 'Authorization':'Bearer '+auth['access_token'],
                           'ChatGPT-Account-ID':auth['account_id'], 'OpenAI-Beta':'responses=experimental',
                           'originator':'codex_cli_rs', 'User-Agent':'codex_cli_rs/0.163.0'}
                remote.request('POST','/backend-api/codex/responses',json.dumps(body).encode(),headers)
                response = remote.getresponse()
                record.update(http_status=response.status)
                self.send_response(response.status)
                self.send_header('Content-Type',response.getheader('Content-Type','text/event-stream'))
                self.end_headers()
                pending = b''
                while True:
                    chunk = response.read1(16384)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    pending += chunk
                    while b'\n' in pending:
                        line,_,pending = pending.partition(b'\n')
                        if line.startswith(b'data: {'):
                            item = json.loads(line[6:])
                            if item.get('type') == 'response.completed':
                                record['usage'] = item['response'].get('usage')
                                record['returned_model'] = item['response'].get('model')
                remote.close()
                record['status'] = 'completed' if response.status==200 else 'http_error'
            except Exception as error:
                record.update(status='failed', error=type(error).__name__)
            finally:
                record['wall_seconds'] = time.perf_counter()-started
                Path(audit).write_text(json.dumps(record, indent=2)+'\n')

    class Server(socketserver.UnixStreamServer):
        allow_reuse_address = True
    server = Server(str(socket_path),Handler)
    os.chmod(socket_path,0o600)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    return server


def launch_cli():
    resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
    resource.setrlimit(resource.RLIMIT_CPU,(45,45))
    resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    lib = ctypes.CDLL('libseccomp.so.2')
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7fff0000)
    for name in (b'fork',b'vfork',b'clone3'):
        number = lib.seccomp_syscall_resolve_name(name)
        code = errno.ENOSYS if name==b'clone3' else errno.EPERM
        if number >= 0 and lib.seccomp_rule_add(context,0x50000|code,number,0):
            raise RuntimeError('CLI process filter failed')
    class Arg(ctypes.Structure):
        _fields_=[('arg',ctypes.c_uint),('op',ctypes.c_int),('a',ctypes.c_uint64),('b',ctypes.c_uint64)]
    lib.seccomp_rule_add_array.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint,ctypes.POINTER(Arg)]
    # clone is permitted for threads only, never a new process.
    if lib.seccomp_rule_add_array(context,0x50000|errno.EPERM,lib.seccomp_syscall_resolve_name(b'clone'),1,
                                 ctypes.byref(Arg(0,7,0x10000,0))):
        raise RuntimeError('CLI clone filter failed')
    if ctypes.CDLL(None).prctl(38,1,0,0,0) or lib.seccomp_load(context):
        raise RuntimeError('CLI seccomp failed')
    os.execv('/cli',['/cli']+sys.argv[2:])


def bridge():
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*_):
            pass
        def do_POST(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            remote=UnixConnection('/model.sock')
            remote.request('POST',self.path,body,{'Content-Type':'application/json'})
            response=remote.getresponse()
            self.send_response(response.status)
            self.send_header('Content-Type',response.getheader('Content-Type','text/event-stream'))
            self.end_headers()
            while True:
                chunk=response.read1(16384)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            remote.close()
    server=http.server.HTTPServer(('127.0.0.1',28731),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    os.environ.update(CODEX_HOME='/output/codex',HOME='/output/home',OPENAI_API_KEY='broker-placeholder',
                      TOKIO_WORKER_THREADS='2',RAYON_NUM_THREADS='2')
    result=subprocess.run(['/usr/local/bin/python3','-I','-B','/relay.py','launch']+sys.argv[2:])
    server.shutdown()
    raise SystemExit(result.returncode)


if __name__=='__main__':
    if sys.argv[1]=='launch':
        launch_cli()
    elif sys.argv[1]=='bridge':
        bridge()
