"""Rescore saved outputs with frozen geometry; no generated code or model call."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scene_rsi.continuous.geometry import MeshWorld
from scene_rsi.world.generator_benchmark import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--geometry',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    assert (args.run/'finished.json').exists()
    cases = json.loads((args.run/'private_tasks.json').read_text())
    count,skipped,error = 0,0,0.
    for folder in [*args.run.glob('baseline_*'),*args.run.glob('c[0-9]*')]:
        for split in ('dev','selection','test'):
            if not (folder/split).exists(): continue
            for path in sorted((folder/split).glob('[0-9][0-9]_[0-9].json')):
                row = json.loads(path.read_text())
                if row['state'] is None:
                    skipped += 1
                    continue
                world = MeshWorld(cases[split][row['task_index']]['task'],args.geometry)
                try:
                    result = world.evaluate(row['state'],final=True)
                finally:
                    world.close()
                assert result['success']==row['result']['success'] and result['failures']==row['result']['failures']
                for key,value in result['quality'].items():
                    error = max(error,abs(value-row['result']['quality'][key]))
                assert error < 1e-9
                count += 1
    save(args.out,{'saved_states_rescored':count,'missing_states_skipped':skipped,'hard_results_match':True,
                   'max_quality_error':error,'model_calls':0,'candidate_executions':0})
    print('rescored',count,'missing',skipped,'max error',error)


if __name__=='__main__':
    main()
