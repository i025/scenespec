"""Interactive pushing world CLI, separate from the original static benchmark."""
import argparse
import base64
from io import BytesIO
import json
from pathlib import Path

from .task import WorldTask, GeneratorConfig, generate
from .planning import solve
from .engine import World
from .experiments import evaluate, experiment, save
from .server import serve

DEFAULT_CONFIG = Path(__file__).resolve().parents[2]/'examples/world/selected_generator.json'


def export_demo(task, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    save(out/'scene.json', task.to_dict())
    reference = solve(task)
    if reference["status"] != "solvable":
        raise ValueError("demo task must be provably solvable")
    frames, records = [], []
    with World(task) as world:
        frames.append(world.render())
        records.append(world.snapshot())
        for action in reference["actions"]:
            records.append(world.step(action))
            frames.append(world.render())
        if not world.success or world.collisions:
            raise ValueError("reference failed in the actual collision world")
    frames[0].save(out/'start.png')
    frames[-1].save(out/'goal.png')
    frames[0].save(out/'solution.gif', save_all=True, append_images=frames[1:]+[frames[-1]]*3,
                   duration=240, loop=0)
    save(out/'trajectory.json', records)
    images = []
    for frame in frames:
        buffer = BytesIO()
        frame.save(buffer, 'PNG')
        images.append('data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode())
    # Self-contained review artifact; the live server supplies manual interaction.
    (out/'replay.html').write_text('''<!doctype html><meta charset="utf-8"><title>Code World · CPU replay</title>
<style>body{background:#101b25;color:#edf1ee;font:16px system-ui;max-width:960px;margin:35px auto}img{width:100%;border-radius:12px}button{padding:12px 24px;border:0;border-radius:6px;margin:12px 8px 0 0;cursor:pointer}p{color:#aabcb9}</style>
<h1>Code World / recorded solution</h1><p>Actual PyBullet TinyRenderer frames · CPU only. For keyboard interaction, run the live server.</p>
<img id="frame"><div><button onclick="step(-1)">Previous</button><button onclick="step(1)">Next</button><button onclick="playing=!playing">Play / pause</button><span id="count"></span></div>
<script>const frames='''+json.dumps(images)+''';let i=0,playing=false;function step(d){i=(i+d+frames.length)%frames.length;document.getElementById('frame').src=frames[i];document.getElementById('count').textContent=i+' / '+(frames.length-1)}setInterval(()=>{if(playing)step(1)},300);step(0)</script>''')
    return {"steps": len(reference["actions"]), "success": True, "render": "PyBullet TinyRenderer CPU"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('generate', 'evaluate', 'demo'):
        cmd = commands.add_parser(name)
        cmd.add_argument('--out', type=Path, required=True)
        cmd.add_argument('--task', type=Path)
        cmd.add_argument('--seed', type=int, default=1)
        cmd.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    trial = commands.add_parser('experiment')
    trial.add_argument('--out', type=Path, required=True)
    trial.add_argument('--proposals', type=Path, required=True)
    web = commands.add_parser('serve')
    web.add_argument('--port', type=int, default=8765)
    web.add_argument('--task', type=Path)
    web.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    if args.command == 'serve':
        serve(args.port, WorldTask.from_dict(json.loads(args.task.read_text())) if args.task else None,
              GeneratorConfig(**json.loads(args.config.read_text())))
        return
    if args.command == 'experiment':
        result = experiment(args.out, json.loads(args.proposals.read_text()))
    else:
        task = (WorldTask.from_dict(json.loads(args.task.read_text())) if args.task else
                generate(args.seed, GeneratorConfig(**json.loads(args.config.read_text()))))
        if args.command == 'generate':
            save(args.out, task.to_dict())
            result = {"out": str(args.out), "solvability": solve(task)['status']}
        elif args.command == 'demo':
            result = export_demo(task, args.out)
        else:
            result = evaluate(task)
            save(args.out, result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
