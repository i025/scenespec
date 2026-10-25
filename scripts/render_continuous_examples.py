"""Four predeclared DEV examples and one DEV failure; no references or test scenes."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scene_rsi.continuous.render import export
from scene_rsi.continuous.tasks import FAMILIES
from scene_rsi.world.generator_benchmark import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--geometry',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--blender',required=True)
    parser.add_argument('--gpu')
    args = parser.parse_args()
    cases = json.loads((args.run/'private_tasks.json').read_text())['dev']
    args.out.mkdir(parents=True,exist_ok=False)
    samples = [(next(i for i,c in enumerate(cases) if c['task']['family'] == family),'joint','sample_'+family) for family in FAMILIES]
    for i in range(len(cases)):
        row = json.loads((args.run/'dev'/f'{i:02d}_0_random.json').read_text())
        if not row['result']['success']:
            samples.append((i,'random','failure_random'))
            break
    records = []
    for i,method,name in samples:
        task = cases[i]['task']
        row = json.loads((args.run/'dev'/f'{i:02d}_0_{method}.json').read_text())
        result = export(task,row['state'],args.geometry,args.out/name,blender=args.blender,
                        device='auto' if args.gpu else 'cpu',gpu=args.gpu,size=768,samples=24,threads=4)
        if result['verification']['success'] != row['result']['success']:
            raise AssertionError('render recheck changed the recorded success status')
        save(args.out/name/'task.json',task)
        save(args.out/name/'output_state.json',row['state'])
        records.append({'directory':name,'dev_id':task['spec']['scene_id'],'method':method,
                        'success':row['result']['success'],'quality':row['result']['quality'],
                        'failures':row['result']['failures'],'render':result['render']})
    save(args.out/'examples.json',records)
    montage(args.out,records)


def montage(out,records):
    from PIL import Image,ImageDraw,ImageFont
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',20)
    sheet = Image.new('RGB',(1536,len(records)*548+40),'#17242d')
    draw = ImageDraw.Draw(sheet)
    for j,title in enumerate(('Fixed task camera','Room overview','Top')):
        draw.text((j*512+12,8),title,font=font,fill='white')
    for i,row in enumerate(records):
        y = 40+i*548
        draw.text((12,y),row['directory']+' | '+row['method']+' | '+('valid' if row['success'] else 'failure'),font=font,fill='white')
        for j,view in enumerate(('overview','reverse','top')):
            image = Image.open(out/row['directory']/(view+'.png')).convert('RGB').resize((512,512),Image.Resampling.LANCZOS)
            sheet.paste(image,(j*512,y+30))
    sheet.save(out/'contact_sheet.jpg',quality=92)


if __name__ == '__main__':
    main()
