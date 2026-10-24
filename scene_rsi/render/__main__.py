"""python -m scene_rsi.render --task world.json --out new-directory"""
import argparse
import json

from ..schema import SceneSpec, SceneState
from . import export_scene


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True, help='existing WorldTask JSON (spec + initial)')
    parser.add_argument('--state', help='optional later SceneState JSON; initial is the default')
    parser.add_argument('--out', required=True)
    parser.add_argument('--blender', default='blender')
    parser.add_argument('--device', choices=('auto', 'cpu', 'cuda'), default='auto')
    parser.add_argument('--gpu', help='one visible GPU index or UUID; choose a free device')
    parser.add_argument('--size', type=int, default=768)
    parser.add_argument('--samples', type=int, default=32)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    with open(args.task) as stream:
        task = json.load(stream)
    spec = SceneSpec.from_dict(task['spec'])
    if args.state:
        state = SceneState.load(args.state)
    else:
        state = SceneState.from_dict(task['initial'])
    result = export_scene(spec, state, args.out, args.blender, args.device, args.gpu,
                          args.size, args.samples, args.threads)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
