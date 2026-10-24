"""Independent frozen-generator experiment; does not write generator code."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scene_rsi.world.generalization import cases, map_feasibility, evaluate_cases
from scene_rsi.world.generator_benchmark import save

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    dataset = cases()
    for case in dataset:
        case['feasibility'] = map_feasibility(case['packet'])
    save(args.out/'conditions.json', dataset)
    report = {'frozen_generator_git':'b83b104', 'tuning_on_this_set':False,
              'feasible':sum(c['feasibility']['status'] == 'feasible' for c in dataset),
              'impossible':sum(c['feasibility']['status'] == 'impossible' for c in dataset)}
    for label, path in [('original', ROOT/'experiments/first_algorithm/baseline_layout.py'),
                        ('frozen', ROOT/'scene_rsi/agent/world_generator.py')]:
        report[label], _ = evaluate_cases(path.read_text(), dataset, args.out, args.out/label)
        print(label, report[label]['feasible_maps'], flush=True)
    save(args.out/'summary.json', report)


if __name__ == '__main__':
    main()
