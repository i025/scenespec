"""Generate one scene with the selected algorithm, exclusively in its restricted process."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import random
import shutil
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scene_rsi.meta.layout_process import LayoutProcess
from scene_rsi.world.algorithm_experiment import audit,build_task
from scene_rsi.world.task import GeneratorConfig


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',required=True,type=int)
    parser.add_argument('--width',default=6,type=int)
    parser.add_argument('--depth',default=6,type=int)
    parser.add_argument('--obstacles',default=6,type=int)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    config=GeneratorConfig(args.width,args.depth,args.obstacles,require_solvable=False)
    config.validate()
    source=ROOT/'scene_rsi/agent/world_generator.py'
    audit(source.read_text())
    if args.out.exists():
        raise FileExistsError(args.out)
    with TemporaryDirectory(prefix='scene-algorithm-') as temp:
        root=Path(temp);payload=root/'payload';payload.mkdir()
        shutil.copyfile(source,payload/'layout.py')
        process=LayoutProcess(payload,root)
        rng=random.Random(args.seed)
        cells=[(x,y) for y in range(args.depth) for x in range(args.width)]
        try:
            for attempt in range(256):
                sample=rng.sample(cells,args.obstacles+3)
                response=process.request({'width':args.width,'depth':args.depth,
                                           'obstacles':args.obstacles,'sample':sample})
                task=build_task(response,sample,config,args.seed,attempt)
                if task is not None:
                    break
            else:
                raise RuntimeError('256 raw proposals exhausted')
        finally:
            execution=process.close()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(task.to_dict(),indent=2)+'\n')
    args.out.with_suffix('.execution.json').write_text(json.dumps(
        {'seed':args.seed,'config':asdict(config),'sample_attempts':attempt+1,
         'oracle_filter_calls':0,'execution':execution},indent=2)+'\n')
    print(args.out)


if __name__=='__main__':
    main()
