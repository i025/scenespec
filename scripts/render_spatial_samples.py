"""Render actual successful DEV outputs of a selection-chosen candidate, never witnesses."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_rsi.render import export_spatial
from scene_rsi.world.generator_benchmark import save


def contact_sheet(out, slot=0):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 23)
    sheet = Image.new('RGB', (1536, 1694), '#17242d')
    draw = ImageDraw.Draw(sheet)
    for j, title in enumerate(('Task camera', 'Room overview', 'Top')):
        draw.text((j*512+12, 12), title, font=font, fill='white')
    for i, (family, title) in enumerate((('support','3D support'), ('rooms','Rooms and passage'),
                                       ('visibility','Visibility and occlusion'))):
        y = 50+i*548
        draw.text((12, y), title+f' | actual dev output, candidate {slot}', font=font, fill='white')
        for j, view in enumerate(('overview','reverse','top')):
            picture = Image.open(out/f'seed_{family}'/f'{view}.png').convert('RGB')
            sheet.paste(picture.resize((512,512), Image.Resampling.LANCZOS), (j*512,y+32))
    sheet.save(out/'contact_sheet.jpg', quality=92)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--slot', type=int, required=True, help='candidate chosen using selection scores')
    parser.add_argument('--blender', required=True)
    parser.add_argument('--gpu', help='one idle GPU UUID; omit for CPU rendering')
    args = parser.parse_args()
    if not (args.run/'proposals_complete.json').exists():
        raise RuntimeError('render after proposals are closed')
    tasks = json.loads((args.run/'private_tasks.json').read_text())
    lookup = {c['id']:c for c in tasks['common']+sum(tasks['probes'].values(), [])}
    call = next(c for c in json.loads((args.run/'calls.json').read_text()) if c['slot'] == args.slot)
    path = args.run/call['trial']/f"r{call['round']}"/'candidate_dev'/'rows.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    args.out.mkdir(parents=True, exist_ok=False)
    records = []
    for family in ('support','rooms','visibility'):
        # Most populated successful scene, dev-only, no cherry-picking test quality.
        candidates = [r for r in rows if r['success'] and r['family'] == family]
        if not candidates:
            raise ValueError('no successful dev example for '+family)
        row = max(candidates, key=lambda r:len(r['scene']['state']['objects']))
        case = lookup[row['id']]
        out = args.out/f'seed_{family}'
        result = export_spatial(case, row['scene'], out, blender=args.blender,
                                gpu=args.gpu, device='auto' if args.gpu else 'cpu',
                                size=768, samples=24, threads=4)
        save(out/'task.json', case)
        save(out/'output_state.json', row['scene'])
        records.append({'family':family, 'dev_id':row['id'], 'candidate_slot':args.slot,
                        'source':'actual candidate_dev output; not a reference witness',
                        'verification':result['verification'], 'render':result.get('render')})
    save(args.out/'examples.json', records)
    contact_sheet(args.out, args.slot)


if __name__ == '__main__':
    main()
