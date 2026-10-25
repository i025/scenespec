"""Trusted baseline benchmark. No model, candidate-code loading, or meta evolution."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import random
import resource
import secrets
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scene_rsi.continuous.baselines import generate,METHODS
from scene_rsi.continuous.geometry import MeshWorld
from scene_rsi.continuous.tasks import make_task,FAMILIES
from scene_rsi.world.generator_benchmark import save


def worker(args):
    # Only the three repository-authored functions can run. This is NOT a
    # replacement sandbox for arbitrary generated Python. Shared hard ceilings.
    resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
    resource.setrlimit(resource.RLIMIT_CPU,(45,45))
    request = json.loads(args.worker.read_text())
    task = request['task']
    world = MeshWorld(task,args.geometry)
    try:
        state,cost = generate(task,world,args.method,request['seed'])
    finally:
        world.close()
    independent = MeshWorld(task,args.geometry)
    try:
        result = independent.evaluate(state,final=True)
    finally:
        independent.close()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    save(args.out,{'state':state,'generation':cost,'result':result,
                   'worker_cpu_seconds':usage.ru_utime+usage.ru_stime,'peak_rss_mb':usage.ru_maxrss/1024})


def summarize(rows):
    quality = rows[0]['result']['quality'].keys()
    return {'runs':len(rows),'successes':sum(r['result']['success'] for r in rows),
        'success_rate':statistics.mean(r['result']['success'] for r in rows),
        'failures':dict(Counter(f['type'] for r in rows for f in r['result']['failures'])),
        'quality':{key:{'mean':statistics.mean(r['result']['quality'][key] for r in rows),
                        'sample_sd':statistics.stdev(r['result']['quality'][key] for r in rows)} for key in quality},
        'generation_cpu_seconds_mean':statistics.mean(r['generation']['cpu_seconds'] for r in rows),
        'generation_wall_seconds_mean':statistics.mean(r['generation']['wall_seconds'] for r in rows),
        'queries_min':min(r['generation']['queries'] for r in rows),'queries_max':max(r['generation']['queries'] for r in rows),
        'worker_cpu_seconds_total':sum(r['worker_cpu_seconds'] for r in rows),
        'peak_worker_rss_mb':max(r['peak_rss_mb'] for r in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path)
    parser.add_argument('--geometry',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--split',choices=('dev','val','test'))
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--method',choices=METHODS)
    parser.add_argument('--out',type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    run = args.run.resolve()
    run.mkdir(parents=True,exist_ok=True)
    os.chmod(run,0o700)
    if args.prepare:
        if (run/'private_tasks.json').exists():
            raise RuntimeError('private split already prepared')
        rng = secrets.SystemRandom()
        cases,checked = {},[]
        for split,count in (('dev',8),('val',8),('test',12)):
            cases[split] = []
            for index in range(count):
                seed = rng.getrandbits(64)
                task,reference = make_task(seed,FAMILIES[index%4],f'{split}_{index:02d}')
                world = MeshWorld(task,args.geometry)
                try:
                    verdict = world.evaluate(reference,final=True)
                finally:
                    world.close()
                checked.append({'id':task['spec']['scene_id'],'success':verdict['success'],'failures':verdict['failures']})
                cases[split].append({'seed':seed,'task':task,'reference':reference,
                                      'algorithm_seeds':[rng.getrandbits(32) for _ in range(2)]})
        save(run/'private_tasks.json',cases)
        save(run/'private_reference_checks.json',checked)
        if not all(r['success'] for r in checked):
            raise RuntimeError('reference construction failed; no baseline evaluation permitted')
        save(run/'reference_acceptance.json',{'tasks':len(checked),'legal_references':len(checked),
                                            'hidden_data_published':False})
        print('Prepared private splits with',len(checked),'verified feasible witnesses')
        return
    if not args.split or not (run/'reference_acceptance.json').exists():
        raise RuntimeError('prepare and validate private reference layouts first')
    cases = json.loads((run/'private_tasks.json').read_text())[args.split]
    destination = run/args.split
    destination.mkdir(exist_ok=False)
    rows = {method:[] for method in METHODS}
    started = time.perf_counter()
    for index,case in enumerate(cases):
        repeats = 2 if args.split == 'test' else 1
        for repeat in range(repeats):
            # Counterbalance process order without sharing caches or warm starts.
            for method in (METHODS if (index+repeat)%2 == 0 else METHODS[::-1]):
                stem = f'{index:02d}_{repeat}_{method}'
                request,out = destination/(stem+'_input.json'),destination/(stem+'.json')
                save(request,{'task':case['task'],'seed':case['algorithm_seeds'][repeat]})
                command = [sys.executable,'-B',str(Path(__file__).resolve()),'--worker',str(request),
                           '--method',method,'--geometry',str(args.geometry.resolve()),'--out',str(out)]
                env = dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',CUDA_VISIBLE_DEVICES='')
                process = subprocess.run(command,env=env,capture_output=True,timeout=90)
                if process.returncode:
                    (destination/(stem+'.stderr')).write_bytes(process.stderr)
                    raise RuntimeError('trusted baseline worker failed: '+stem)
                row = json.loads(out.read_text())
                rows[method].append(row)
                print(args.split,stem,row['result']['success'],round(row['generation']['cpu_seconds'],3),flush=True)
    summary = {'methods':{method:summarize(values) for method,values in rows.items()},
               'wall_seconds':time.perf_counter()-started,'model_calls':0,
               'tasks':len(cases),'repeats_per_task':2 if args.split == 'test' else 1}
    save(destination/'summary.json',summary)


if __name__ == '__main__':
    main()
