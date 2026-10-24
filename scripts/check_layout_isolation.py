"""Explicit live acceptance for the one-process experiment runner. No model call."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scene_rsi.meta.layout_process import LayoutProcess

CASES={
    'private_file': "def layout(p):\n try: open('/home/xymo/.codex/auth.json').read()\n except PermissionError: return {'denied':True}\n return {'denied':False}\n",
    'network': "def layout(p):\n import _socket\n try: _socket.socket()\n except PermissionError: return {'denied':True}\n return {'denied':False}\n",
    'fork': "def layout(p):\n import os\n try: os.fork()\n except PermissionError: return {'denied':True}\n return {'denied':False}\n",
    'verifier_write': "def layout(p):\n try: open('/scene_rsi/evaluator/verifier.py','w').write('changed')\n except PermissionError: return {'denied':True}\n return {'denied':False}\n",
    'raise_limits': "def layout(p):\n import resource\n try: resource.setrlimit(resource.RLIMIT_AS,(2**40,2**40))\n except (PermissionError,ValueError): return {'denied':True}\n return {'denied':False}\n",
    'memory': "def layout(p):\n try: items=[bytearray(64*1024**2) for i in range(16)]\n except MemoryError: return {'denied':True}\n return {'denied':False}\n",
    'output': "def layout(p):\n while True: print('x'*2048,flush=True)\n",
    'infinite_loop': "def layout(p):\n while True: pass\n",
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    rows=[]
    for name,source in CASES.items():
        payload=args.out/name
        payload.mkdir()
        (payload/'layout.py').write_text(source)
        process=LayoutProcess(payload,args.out)
        error,result=None,None
        try:
            result=process.request({})
        except (TimeoutError,ValueError,RuntimeError,BrokenPipeError) as exception:
            error=str(exception)
        finally:
            usage=process.close()
        passed=(result=={'denied':True} if name not in ('output','infinite_loop') else error is not None)
        rows.append({'case':name,'passed':passed,'result':result,'error':error,'resources':usage})
        print(name,passed,flush=True)
    (args.out/'acceptance.json').write_text(json.dumps(rows,indent=2)+'\n')
    if not all(row['passed'] for row in rows):
        raise SystemExit('isolation acceptance failed')


if __name__=='__main__':
    main()
