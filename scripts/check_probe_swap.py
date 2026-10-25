"""Controlled choice-mechanism check, NOT a model optimization experiment."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_metaimp import policy_decision
from run_spatial_ablation import ARMS, POLICIES
from scene_rsi.world.generator_benchmark import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    results = []
    for name, successes in (('weak',0),('partial',4),('reliable',6)):
        packet = {'phase':'choose', 'summary':{'tasks':6,'valid':6,'solvable':successes,'solvable_rate':successes/6}}
        decisions = {policy:policy_decision((ROOT/file).read_text(), packet, args.out/name/policy, args.out)
                     for policy, file in POLICIES.items()}
        choices = {arm:decisions[choice]['experiment'] for arm, (_, choice) in ARMS.items()}
        assert choices['M0'] == choices['M1_random'] == 'random'
        assert choices['M0_M1probe'] == choices['M1'] == {'weak':'random','partial':'barriers','reliable':'dense'}[name]
        results.append({'synthetic_condition':name, 'same_input_for_both_policies':packet, 'choices':choices})
    save(args.out/'mechanism_check.json', {'model_calls':0, 'performance_evidence':False, 'results':results})
    print(json.dumps(results))


if __name__ == '__main__':
    main()
