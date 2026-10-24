"""Trusted single-process entry, inside an existing Apptainer image only.

The candidate supplies layout(packet)->dict. It never receives scene evaluators,
credentials or filesystem access. Limits are installed before any candidate code.
"""
import collections
import _socket
import ctypes
import errno
import heapq
import itertools
import json
import math
import os
import random
import resource
import sys

source = open('/payload/layout.py').read(131073)
if len(source) > 131072:
    raise ValueError('candidate source exceeds limit')
resource.setrlimit(resource.RLIMIT_AS, (256*1024**2, 256*1024**2))
resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
observed_limits = {name: resource.getrlimit(getattr(resource, name)) for name in
                   ('RLIMIT_AS', 'RLIMIT_CPU', 'RLIMIT_FSIZE', 'RLIMIT_NPROC')}
code = compile(source, 'layout.py', 'exec')
os.closerange(3, 65536)
lib = ctypes.CDLL('libseccomp.so.2')
lib.seccomp_init.restype = ctypes.c_void_p
lib.seccomp_init.argtypes = [ctypes.c_uint32]
lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
lib.seccomp_load.argtypes = [ctypes.c_void_p]
lib.seccomp_release.argtypes = [ctypes.c_void_p]
lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
context = lib.seccomp_init(0x7fff0000)  # ALLOW, with explicit kernel denials below.
for name in '''open openat openat2 creat fork vfork clone clone3 execve execveat
socket socketpair connect bind listen accept accept4 sendto sendmsg sendmmsg
ptrace process_vm_readv process_vm_writev kill tkill tgkill pidfd_send_signal
mount umount2 pivot_root chroot unshare setns setsid setpgid setrlimit prlimit64
bpf perf_event_open userfaultfd keyctl add_key request_key io_uring_setup
mkdir mkdirat mknod mknodat link linkat symlink symlinkat rename renameat renameat2
unlink unlinkat truncate ftruncate chmod fchmod fchmodat chown fchown fchownat
setuid setgid setresuid setresgid capset init_module finit_module delete_module
shmget shmat shmctl shmdt msgget msgsnd msgrcv semget semop semtimedop semctl
reboot kexec_load'''.split():
    number = lib.seccomp_syscall_resolve_name(name.encode())
    if number >= 0 and lib.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0):
        raise RuntimeError('cannot install syscall rule: '+name)
if ctypes.CDLL(None).prctl(38, 1, 0, 0, 0) != 0 or lib.seccomp_load(context):
    raise RuntimeError('cannot enforce no_new_privs/seccomp')
lib.seccomp_release(context)
print(json.dumps({'limits': observed_limits, 'ready': True, 'address_space_bytes': 256*1024**2, 'cpu_seconds': 5,
                  'file_bytes': 0, 'process_creation': 'denied', 'file_open': 'denied',
                  'network': 'denied'}), flush=True)
namespace = {}
exec(code, namespace)
for line in sys.stdin:
    packet = json.loads(line)
    result = namespace['layout'](packet)
    print(json.dumps(result, allow_nan=False), flush=True)
