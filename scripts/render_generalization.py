"""Render the protocol's two fixed representative map pairs, without score selection."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scene_rsi.render import export_scene
from scene_rsi.world.task import WorldTask
from scene_rsi.world.generator_benchmark import save
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--blender', required=True)
    parser.add_argument('--gpu')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    conditions = json.loads((args.run/'conditions.json').read_text())
    rows = {label:{r['id']:r for r in (json.loads(line) for line in
            (args.run/label/'rows.jsonl').read_text().splitlines())} for label in ('original', 'frozen')}
    selected = [next(c for c in conditions if c['condition'] == name and
                     c['feasibility']['status'] == 'feasible') for name in ('open', 'slalom')]
    report = []
    for case in selected:
        pair = {'id':case['id'], 'selection':'first feasible in registered group', 'renders':{}}
        for label in ('original', 'frozen'):
            row = rows[label][case['id']]
            task = WorldTask.from_dict(row['scene'])
            destination = args.out/(case['id']+'_'+label)
            result = export_scene(task.spec, task.initial, destination, args.blender, 'auto', args.gpu, 768, 32)
            save(destination/'task.json', task.to_dict())
            pair['renders'][label] = {'directory':destination.name, 'solvability':row['solvability'],
                                     'shortest_actions':row['reference_steps'], 'render':result['render']}
        report.append(pair)
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 19)
    canvas = Image.new('RGB', (1000, 1110), '#101c27')
    draw = ImageDraw.Draw(canvas)
    draw.text((16, 12), 'FROZEN GENERALIZATION  /  SAME OBSTACLES, ONE OUTPUT', font=font, fill='#e6eef3')
    draw.text((16, 44), 'ORIGINAL GENERATOR', font=font, fill='#d9e3e7')
    draw.text((512, 44), 'FROZEN CODEX GENERATOR', font=font, fill='#9fe3ba')
    for index, pair in enumerate(report):
        y = 78+index*512
        for column, label in enumerate(('original', 'frozen')):
            info = pair['renders'][label]
            with Image.open(args.out/info['directory']/'reverse.png') as frame:
                canvas.paste(frame.convert('RGB').resize((480, 480), Image.Resampling.LANCZOS), (12+column*496, y))
            draw.text((16+column*496, y+483), pair['id']+' / '+info['solvability'], font=font, fill='#d9e8ed')
    canvas.save(args.out/'comparison.jpg', quality=94)
    save(args.out/'summary.json', report)


if __name__ == '__main__':
    main()
