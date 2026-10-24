"""One experiment-specific, single-process Apptainer transport; no host fallback."""
import json
import os
from pathlib import Path
import resource
import selectors
import signal
import subprocess
import time

RUNTIME = Path('/srv/lab-disks/disk2/users/xymo/projects/BlenderWorld/runtime/apptainer/bin/apptainer')
IMAGE = Path('/srv/lab-disks/disk2/users/xymo/projects/BlenderWorld/runtime/harbor-images/blenderworld-agent.sif')
ENTRY = Path(__file__).resolve().parents[2]/'scripts/restricted_layout.py'


def container_command(run, binds):
    run = Path(run).resolve()
    for name in ('home', 'cache', 'tmp'):
        (run/name).mkdir(parents=True, exist_ok=True)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(run/'home'),
           'APPTAINER_CACHEDIR': str(run/'cache'), 'APPTAINER_TMPDIR': str(run/'tmp'),
           'APPTAINER_IGNORE_SUBUID': '1'}
    command = [str(RUNTIME), 'exec', '--userns', '--containall', '--no-home',
               '--no-mount', 'hostfs,bind-paths,cwd', '--net', '--network', 'none',
               '--no-privs', '--pwd', '/']
    for source, destination, mode in binds:
        command += ['--bind', f'{Path(source).resolve()}:{destination}:{mode}']
    return command+[str(IMAGE)], env


class LayoutProcess:
    def __init__(self, payload, run):
        command, env = container_command(run, [(payload, '/payload', 'ro'), (ENTRY, '/entry.py', 'ro')])
        self.started = time.perf_counter()
        self.cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_utime + resource.getrusage(resource.RUSAGE_CHILDREN).ru_stime
        self.process = subprocess.Popen(command+['/usr/local/bin/python3', '-I', '-B', '/entry.py'],
                                        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        self.selector = selectors.DefaultSelector()
        for stream in (self.process.stdout, self.process.stderr):
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ)
        self.buffer, self.stderr = bytearray(), bytearray()
        self.output_bytes = 0
        try:
            ready = self.read_line()
            if not (ready.get('ready') is True and ready['limits']['RLIMIT_AS'] == [268435456]*2
                    and ready['limits']['RLIMIT_CPU'] == [5, 5]
                    and ready['limits']['RLIMIT_NPROC'] == [0, 0]):
                raise RuntimeError('kernel limits were not installed')
        except BaseException:
            self.close()
            raise

    def read_line(self):
        deadline = min(self.started+30, time.perf_counter()+7)
        while b'\n' not in self.buffer:
            if time.perf_counter() >= deadline:
                raise TimeoutError('candidate wall time exceeded')
            for key, _ in self.selector.select(.1):
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    continue
                if key.fileobj is self.process.stderr:
                    self.stderr.extend(chunk)
                    if len(self.stderr) > 65536:
                        raise ValueError('candidate stderr exceeded limit')
                else:
                    self.buffer.extend(chunk)
                    self.output_bytes += len(chunk)
                    if self.output_bytes > 1048576 or len(self.buffer) > 16384:
                        raise ValueError('candidate stdout exceeded limit')
            if not self.selector.get_map():
                raise RuntimeError('candidate exited without a complete response: '+self.stderr.decode(errors='replace')[:1000])
        line, _, remaining = self.buffer.partition(b'\n')
        self.buffer = bytearray(remaining)
        return json.loads(line)

    def request(self, packet):
        self.process.stdin.write(json.dumps(packet).encode()+b'\n')
        self.process.stdin.flush()
        return self.read_line()

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()
        self.selector.close()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        return {'exit_code': self.process.returncode, 'wall_seconds': time.perf_counter()-self.started,
                'runtime_cpu_seconds': after.ru_utime+after.ru_stime-self.cpu_before,
                'children_rss_high_water_bytes': after.ru_maxrss*1024,
                'resource_scope': 'Apptainer and waited children; RSS is controller lifetime child high-water mark',
                'stderr': self.stderr.decode(errors='replace')}
