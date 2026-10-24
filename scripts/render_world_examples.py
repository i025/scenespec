"""Generate, independently evaluate, and visually export three existing pushing tasks."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scene_rsi.render import export_scene
from scene_rsi.world.experiments import evaluate
from scene_rsi.world.task import GeneratorConfig, generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', default='blender')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--gpu')
    parser.add_argument('--device', default='auto', choices=('auto', 'cpu', 'cuda'))
    parser.add_argument('--size', default=768, type=int)
    parser.add_argument('--samples', default=32, type=int)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    results = []
    for seed, width, depth, obstacles in ((11, 6, 6, 6), (22, 7, 5, 8), (33, 5, 7, 5)):
        config = GeneratorConfig(width, depth, obstacles, require_solvable=True)
        task = generate(seed, config)
        evaluation = evaluate(task)
        directory = args.out / f'seed_{seed}'
        rendering = export_scene(task.spec, task.initial, directory, args.blender,
                                 args.device, args.gpu, args.size, args.samples)
        (directory / 'task.json').write_text(json.dumps(task.to_dict(), indent=2) + '\n')
        (directory / 'evaluation.json').write_text(json.dumps(evaluation, indent=2) + '\n')
        results.append({'seed': seed, 'config': asdict(config), 'scene_id': task.spec.scene_id,
                        'evaluation': {k: v for k, v in evaluation.items()
                                       if k not in ('trajectory', 'reference_actions')},
                        'rendering': rendering})
        print(f'{task.spec.scene_id}: {rendering["render"]["backend"]}', flush=True)
    (args.out / 'summary.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
