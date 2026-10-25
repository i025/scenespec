"""Fixed continuous-task transport and selection. Candidate Python stays in Apptainer."""
import ast
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import time

from .baselines import state
from .geometry import MeshWorld
from ..meta.layout_process import LayoutProcess
from ..world.algorithm_experiment import audit as pure_audit
from ..world.generator_benchmark import save


def audit(source):
    # random is already imported BEFORE the existing entry installs seccomp.
    # All other original AST restrictions and the kernel boundary remain active.
    if len(source.encode()) > 131072:
        raise ValueError('source too large')
    class PreloadedRandom(ast.NodeTransformer):
        def visit_Import(self,node):
            node.names = [n for n in node.names if n.name != 'random']
            return node if node.names else None
        def visit_ImportFrom(self,node):
            return None if node.module == 'random' else node
    tree = PreloadedRandom().visit(ast.parse(source))
    # Audit the parsed source minus exactly the permitted random import.
    pure_audit(ast.unparse(tree))
    return {'passed':True,'imports':'original pure modules plus preloaded random','kernel_boundary':'unchanged LayoutProcess'}


def initial_source(method):
    if method not in ('repair','joint'):
        raise ValueError('two frozen initial algorithms only')
    root = Path(__file__).resolve().parents[2]
    return (root/'experiments/continuous_meta/initial.py').read_text().replace("METHOD = 'repair'",f'METHOD = {method!r}')


def values_state(task, values):
    if type(values) is not dict or set(values) != set(task['supports']):
        raise ValueError('all movable support-relative triples required')
    if any(type(v) is not list or len(v)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>10000 for x in v) for v in values.values()):
        raise ValueError('bounded finite support-relative u/v/yaw triples required')
    return state(task,values)


def run_case(payload, case, seed, run, geometry):
    history,memory,process = [],None,None
    started,cpu = time.perf_counter(),time.process_time()
    usage,error,output = None,None,None
    world = MeshWorld(case,geometry)
    try:
        process = LayoutProcess(payload,run)
        query_cpu = 0.
        for index in range(24):
            response = process.request({'phase':'query','task':case,'seed':seed,'query':index,
                                        'history':history,'memory':memory})
            if type(response) is not dict or set(response) != {'values','memory'}:
                raise ValueError('values and memory only')
            proposed = values_state(case,response['values'])
            before = time.process_time()
            report = world.evaluate(proposed)
            query_cpu += time.process_time()-before
            history.append({'values':response['values'],'report':report})
            memory = response['memory']
            if query_cpu > 5:
                raise TimeoutError('fixed Mesh-query CPU budget exceeded')
        response = process.request({'phase':'final','task':case,'seed':seed,'query':24,
                                    'history':history,'memory':memory})
        if type(response) is not dict or set(response) != {'index'} or type(response['index']) is not int or not 0 <= response['index'] < 24:
            raise ValueError('select one of the 24 already evaluated outputs')
        output = values_state(case,history[response['index']]['values'])
    except (ValueError,KeyError,TypeError,RuntimeError,TimeoutError,BrokenPipeError) as exc:
        error = type(exc).__name__+': '+str(exc)[:800]
    finally:
        world.close()
        if process is not None:
            usage = process.close()
    wall,controller_cpu = time.perf_counter()-started,time.process_time()-cpu
    if output is not None:
        independent = MeshWorld(case,geometry)
        try:
            result = independent.evaluate(output,final=True)
        finally:
            independent.close()
    else:
        result = {'success':False,'failures':[{'type':'candidate_runtime','objects':[]}],
                  'quality':{'preference_error_m':10.,'visibility_mean':0.},'navigation':None,'stability':None}
    return {'state':output,'result':result,'error':error,'queries':len(history),
            'trace':[{'values':row['values'],'quality':row['report']['quality'],
                      'optimizer_loss':row['report']['optimizer_loss'],'failures':row['report']['failures']} for row in history],
            'generation_wall_seconds':wall,'controller_cpu_seconds':controller_cpu,'execution':usage}


def evaluate(source, cases, out, run, geometry):
    audit(source)
    out.mkdir(parents=True,exist_ok=False)
    payload = out/'payload'
    payload.mkdir()
    (payload/'layout.py').write_text(source)
    rows = []
    for i,case in enumerate(cases):
        for repeat,seed in enumerate(case['algorithm_seeds']):
            row = run_case(payload,case['task'],seed,run,geometry)
            row.update(task_index=i,repeat=repeat)
            save(out/f'{i:02d}_{repeat}.json',row)
            rows.append(row)
    summary = summarize(cases,rows)
    save(out/'summary.json',summary)
    return summary,rows


def summarize(cases, rows):
    from scripts.analyze_continuous_benchmark import distance
    pairs,valid_pairs,rotation = [],[],[]
    for i,case in enumerate(cases):
        a,b = [r for r in rows if r['task_index']==i]
        if a['state'] is not None and b['state'] is not None:
            delta = distance(case['task'],a['state'],b['state'])
            pairs.append(delta['position_rms_m'])
            rotation.append(delta['rotation_mean_deg'])
            if a['result']['success'] and b['result']['success']:
                valid_pairs.append(delta['position_rms_m'])
    return {'tasks':len(cases),'runs':len(rows),'successes':sum(r['result']['success'] for r in rows),
            'success_rate':statistics.mean(r['result']['success'] for r in rows),
            'preference_error_m':statistics.mean(r['result']['quality']['preference_error_m'] for r in rows),
            'visibility_mean':statistics.mean(r['result']['quality']['visibility_mean'] for r in rows),
            'diversity_position_rms_m':statistics.mean(pairs) if pairs else 0.,
            'diversity_rotation_deg':statistics.mean(rotation) if rotation else 0.,
            'valid_pair_count':len(valid_pairs),'valid_pair_diversity_m':statistics.mean(valid_pairs) if valid_pairs else 0.,
            'failures':dict(Counter(f['type'] for r in rows for f in r['result']['failures'])),
            'generation_wall_seconds_mean':statistics.mean(r['generation_wall_seconds'] for r in rows),
            'controller_cpu_seconds_total':sum(r['controller_cpu_seconds'] for r in rows),
            'runtime_cpu_seconds_total':sum((r['execution'] or {}).get('runtime_cpu_seconds',0) for r in rows),
            'runtime_errors':sum(r['error'] is not None for r in rows),
            'queries_min':min(r['queries'] for r in rows),'queries_max':max(r['queries'] for r in rows)}


def eligible(base,candidate):
    return (candidate['runtime_errors']==0 and candidate['successes']>=base['successes'] and
            candidate['visibility_mean']>=base['visibility_mean']-.01 and
            candidate['diversity_position_rms_m']>=.9*base['diversity_position_rms_m'] and
            candidate['diversity_rotation_deg']>=.8*base['diversity_rotation_deg'] and
            candidate['valid_pair_count']>=base['valid_pair_count'] and
            candidate['valid_pair_diversity_m']>=.9*base['valid_pair_diversity_m'] and
            candidate['preference_error_m']<=base['preference_error_m']-.01)


def choose(base, candidates):
    passing = [row for row in candidates if eligible(base,row['selection'])]
    return min(passing,key=lambda row:(row['selection']['preference_error_m'],-row['selection']['successes'],row['slot'])) if passing else None
