"""Trusted Blender renders of the first dev seed for each exact matched obstacle map."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scene_rsi.world.task import WorldTask
from scene_rsi.render import export_scene
from PIL import Image,ImageDraw,ImageFont


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--blender',required=True)
    parser.add_argument('--gpu')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    selection=json.loads((args.run/'summary.json').read_text())['selected']
    rows=[]
    for path in sorted((args.run/'baseline_restricted/dev').glob('*.jsonl')):
        before=min((json.loads(line) for line in path.read_text().splitlines()),key=lambda row:row['seed'])
        matched=[json.loads(line) for line in (args.run/'matched_maps/dev'/path.name).read_text().splitlines()]
        after=next(row for row in matched if row['seed']==before['seed'])
        tasks=[WorldTask.from_dict(row['scene']) for row in (before,after)]
        if tasks[0].obstacles!=tasks[1].obstacles:
            raise RuntimeError('comparison needs identical obstacles')
        pair={'condition':before['config'],'seed':before['seed'],'candidate':selection,
              'scope':'first dev seed; exact matched static obstacles; no score-based image selection'}
        for label,row,task in zip(('before','after'),(before,after),tasks):
            destination=args.out/f'{path.stem}_{label}'
            result=export_scene(task.spec,task.initial,destination,args.blender,'auto',args.gpu,768,32)
            (destination/'task.json').write_text(json.dumps(task.to_dict(),indent=2)+'\n')
            pair[label]={'solvable':row['solvability'],'reference_steps':row['reference_steps'],
                         'directory':destination.name,'render':result['render']}
        rows.append(pair)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',17)
    large=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',25)
    canvas=Image.new('RGB',(1000,1700),'#101c27');draw=ImageDraw.Draw(canvas)
    draw.text((16,12),'SCENE-RSI  /  AUTOMATIC ALGORITHM CHANGE',font=large,fill='#e6eef3')
    draw.text((16,50),'Same obstacles and conditions | No solvability rejection sampling',font=font,fill='#b8cdd7')
    draw.text((16,80),'BASELINE',font=font,fill='#d9e3e7')
    draw.text((512,80),f'CODEX CANDIDATE {selection}',font=font,fill='#9fe3ba')
    for index,pair in enumerate(rows):
        y=112+index*524
        for column,label in enumerate(('before','after')):
            info=pair[label]
            with Image.open(args.out/info['directory']/'reverse.png') as frame:
                canvas.paste(frame.convert('RGB').resize((480,480),Image.Resampling.LANCZOS),(12+column*496,y))
            c=pair['condition']
            caption=f"{c['width']}x{c['depth']} / {c['obstacles']} obstacles / {info['solvable']}"
            draw.text((16+column*496,y+487),caption,font=font,fill='#d9e8ed')
    canvas.save(args.out/'comparison.jpg',quality=94)
    (args.out/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')


if __name__=='__main__':
    main()
