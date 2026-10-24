"""Post-selection control: one candidate output on each baseline's exact accepted map.

No selection, model call or retry. This separates placement quality from changes
in which RNG draw is accepted by the inherited minimum-distance check.
"""
import argparse
import json
from pathlib import Path
import random
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scene_rsi.meta.layout_process import LayoutProcess
from scene_rsi.world.algorithm_experiment import audit,build_task,assess
from scene_rsi.world.generator_benchmark import summarize,diversity,save
from scene_rsi.world.task import GeneratorConfig


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args()
    run=args.run.resolve()
    selection=json.loads((run/'summary.json').read_text())['selected']
    if selection is None:
        raise SystemExit('no selected candidate')
    payload=run/'attempts'/f'candidate_{selection}'/'payload'
    audit((payload/'layout.py').read_text())
    out=run/'matched_maps';out.mkdir()
    report={}
    for split in ('dev','val'):
        destination=out/split;destination.mkdir()
        all_rows=[];groups=[]
        for path in sorted((run/'baseline_restricted'/split).glob('*.jsonl')):
            baseline=[json.loads(line) for line in path.read_text().splitlines()]
            process=LayoutProcess(payload,run)
            rows=[]
            try:
                for before in baseline:
                    config=GeneratorConfig(**before['config'])
                    cells=[(x,y) for y in range(config.depth) for x in range(config.width)]
                    rng=random.Random(before['seed'])
                    for _ in range(before['sample_attempts']):
                        sample=rng.sample(cells,config.obstacles+3)
                    row={key:before[key] for key in ('seed','config')}
                    row.update(generated=False,generation_error=None,sample_attempts=1,rejected_attempts=0,
                               filter_searches=0,filter_search_seconds=0,
                               rejections={'minimum_distance':0,'corner_option':0,'unsolvable':0,'unknown':0})
                    started,cpu=time.perf_counter(),time.process_time()
                    response=process.request({'width':config.width,'depth':config.depth,
                                              'obstacles':config.obstacles,'sample':sample})
                    task=build_task(response,sample,config,before['seed'],before['sample_attempts']-1)
                    if task is None:
                        raise RuntimeError('selected candidate broke minimum distance on fixed map')
                    row.update(generation_wall_seconds=time.perf_counter()-started,
                               generation_cpu_seconds=time.process_time()-cpu)
                    assess(task,row)
                    rows.append(row)
            finally:
                execution=process.close()
            with (destination/path.name).open('w') as stream:
                for row in rows:stream.write(json.dumps(row)+'\n')
            groups.append({'condition':before['config'],'metrics':summarize(rows),
                           'diversity':diversity(rows),'execution':execution})
            all_rows.extend(rows)
        report[split]={'metrics':summarize(all_rows),'groups':groups}
        print(split,report[split]['metrics']['solvable'],'/ 96',flush=True)
    save(out/'summary.json',{'selected':selection,'scope':'post-hoc exact-obstacle-map control; not used for selection',
                             'oracle_filter_calls':0,'attempts_per_task':1,'results':report})


if __name__=='__main__':
    main()
