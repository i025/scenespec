"""Fixed experiment controller. Candidate modules never enter host sys.path."""
import ast
from dataclasses import asdict
import json
from pathlib import Path
import random
import time

from ..meta.layout_process import LayoutProcess
from . import task as tasks
from .experiments import evaluate
from .generator_benchmark import CONDITIONS, SEEDS, summarize, diversity, save


def audit(source):
    if len(source.encode())>131072:
        raise ValueError('source too large')
    tree=ast.parse(source)
    functions={node.name:node for node in tree.body if isinstance(node,ast.FunctionDef)}
    if 'layout' not in functions or len(functions['layout'].args.args)!=1:
        raise ValueError('layout(packet) required')
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.Import,ast.ImportFrom)):
            continue
        if isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
            continue
        if isinstance(node,ast.Assign):
            ast.literal_eval(node.value)
            continue
        raise ValueError('only functions, imports and literal constants at module scope')
    forbidden={'open','eval','exec','compile','__import__','globals','locals','getattr','setattr','delattr','vars','input','breakpoint'}
    for node in ast.walk(tree):
        if isinstance(node,(ast.Global,ast.Nonlocal)):
            raise ValueError('no cross-task mutable globals')
        if isinstance(node,ast.Name) and (node.id in forbidden or node.id.startswith('__')):
            raise ValueError('forbidden builtin')
        if isinstance(node,ast.Attribute) and node.attr.startswith('_'):
            raise ValueError('private attributes forbidden')
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            names=[item.name for item in node.names] if isinstance(node,ast.Import) else [node.module or '']
            if any(name not in {'math','collections','heapq','itertools'} for name in names):
                raise ValueError('import outside fixed pure-algorithm modules')
    return {'passed':True,'scope':'one generator source file; AST is not the execution boundary'}


def build_task(response,sample,config,seed,attempt):
    if type(response) is not dict or set(response)!={'obstacles','agent','crate','goal'}:
        raise ValueError('layout output needs obstacles/agent/crate/goal only')
    def cell(value):
        if type(value) not in (list,tuple) or len(value)!=2 or any(type(v) is not int for v in value):
            raise ValueError('integer XY cells required')
        return tuple(value)
    obstacles=[cell(value) for value in response['obstacles']]
    if len(obstacles)!=config.obstacles or set(obstacles)!={tuple(value) for value in sample[:-3]}:
        raise ValueError('the sampled static obstacle map must be preserved')
    agent,crate,goal=[cell(response[key]) for key in ('agent','crate','goal')]
    if len({agent,crate,goal})!=3 or set((agent,crate,goal))&set(obstacles):
        raise ValueError('actors/goal must occupy distinct free cells')
    if any(not(0<=x<config.width and 0<=y<config.depth) for x,y in (agent,crate,goal)):
        raise ValueError('cell out of room')
    if sum(abs(a-b) for a,b in zip(crate,goal))<2:
        return None
    return tasks.make_task(config.width,config.depth,obstacles,agent,crate,goal,f'world_{seed}_{attempt}')


def assess(task,row):
    result=evaluate(task)
    state,pushes=task.start,0
    for action in result['reference_actions'] or []:
        state,event=tasks.transition(task,state,action)
        pushes+=event=='push'
    row.update(generated=True,scene=task.to_dict(),valid=True,failures=[],
               solvability=result['solvability'],reference_replayed=result['reference_replayed'],
               reference_steps=len(result['reference_actions']) if result['reference_actions'] is not None else None,
               reference_pushes=pushes if result['solvability']=='solvable' else None,
               crate_goal_manhattan=sum(abs(a-b) for a,b in zip(task.start[1],task.goal)),
               search_states=result['search_states'],assessment_wall_seconds=result['wall_seconds'],
               fixed_agent={key:result[key] for key in ('outcome','success','steps','pushes','collisions','timeout')})


def evaluate_source(payload,run,out,split):
    """Original seeds and raw sample budget. No solvability feedback reaches the process."""
    out=Path(out)
    out.mkdir(parents=True,exist_ok=False)
    rows,groups,resources=[],[],[]
    for width,depth,count in CONDITIONS[split]:
        config=tasks.GeneratorConfig(width,depth,count,require_solvable=False)
        process=LayoutProcess(payload,run)
        group=[]
        try:
            for seed in range(SEEDS[split],SEEDS[split]+32):
                rng=random.Random(seed)
                cells=[(x,y) for y in range(depth) for x in range(width)]
                row={'seed':seed,'config':asdict(config),'generated':False,'generation_error':None,
                     'sample_attempts':0,'rejected_attempts':0,'filter_searches':0,'filter_search_seconds':0,
                     'rejections':{'minimum_distance':0,'corner_option':0,'unsolvable':0,'unknown':0}}
                started,cpu=time.perf_counter(),time.process_time()
                try:
                    for attempt in range(256):
                        sample=rng.sample(cells,count+3)
                        row['sample_attempts']+=1
                        response=process.request({'width':width,'depth':depth,'obstacles':count,'sample':sample})
                        task=build_task(response,sample,config,seed,attempt)
                        if task is not None:
                            break
                        row['rejected_attempts']+=1
                        row['rejections']['minimum_distance']+=1
                    else:
                        raise ValueError('256 raw proposals exhausted')
                    row['generation_wall_seconds']=time.perf_counter()-started
                    # This is trusted controller CPU only; candidate CPU is measured per condition below.
                    row['generation_cpu_seconds']=time.process_time()-cpu
                    assess(task,row)
                except (ValueError,RuntimeError,TimeoutError,BrokenPipeError) as error:
                    row.update(generation_error=str(error)[:1000],generation_wall_seconds=time.perf_counter()-started,
                               generation_cpu_seconds=time.process_time()-cpu)
                group.append(row)
        finally:
            resources.append(process.close())
        with (out/f'{width}x{depth}_{count}.jsonl').open('w') as stream:
            for row in group:
                stream.write(json.dumps(row)+'\n')
        groups.append({'condition':asdict(config),'metrics':summarize(group),'diversity':diversity(group)})
        rows.extend(group)
    report={'split':split,'metrics':summarize(rows),'groups':groups,'execution':resources,
            'cpu_scope':'per-condition runtime_cpu_seconds includes Apptainer and waited child; per-row CPU not attributed',
            'require_solvable':False,'oracle_filter_calls':0,'proposal_budget_per_task':256}
    save(out/'summary.json',report)
    return report
