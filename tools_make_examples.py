from pathlib import Path
import json

ROOT = Path(__file__).parent
assets = {
    'bed_01': dict(width=2.0, depth=1.3, height=.65),
    'desk_01': dict(width=1.35, depth=.65, height=.78),
    'chair_01': dict(width=.52, depth=.55, height=.92),
    'wardrobe_01': dict(width=1.15, depth=.55, height=2.1),
    'sofa_01': dict(width=1.8, depth=.85, height=.9),
    'table_01': dict(width=1.2, depth=.8, height=.75),
    'shelf_01': dict(width=.85, depth=.35, height=1.8),
    'stool_01': dict(width=.42, depth=.42, height=.6),
    'cabinet_01': dict(width=1.5, depth=.65, height=1.15),
    'counter_01': dict(width=1.3, depth=.7, height=.9),
}

def scene(name, width, depth, objs, near, fronts, door, paths):
    return {
        'scene_id': name,
        'room': {'width':width,'depth':depth,'height':3.0},
        'assets': {id_: assets[id_] for id_ in sorted(set(objs.values()))},
        'required_objects': [{'id': k, 'asset':v} for k,v in objs.items()],
        'regions': [{'id':'door','x':door[0],'y':door[1],'width':door[2],'depth':door[3]}],
        'constraints': ([{'type':'near_wall','subject':obj,'wall':wall,'max_gap':.35} for obj,wall in near]
                        + [{'type':'in_front_of','subject':obj,'reference':ref,'max_gap':.4} for obj,ref in fronts]
                        + [{'type':'keep_clear','region':'door'}]),
        'access_paths': [{'from_region':'door','to_object':ob,'clearance':.2,'interaction_distance':.75} for ob in paths]
    }

scenes = [
    scene('bedroom_001',5,4,{'bed':'bed_01','desk':'desk_01','chair':'chair_01','wardrobe':'wardrobe_01'}, [('desk','north')],[('chair','desk')], (.1,.15,.85,.9), ['desk']),
    scene('bedroom_002',5.5,4.5,{'bed':'bed_01','desk':'desk_01','chair':'chair_01'}, [('desk','east')],[('chair','desk')], (.1,1.65,.8,.85), ['desk']),
    scene('office_001',6,4,{'desk':'desk_01','chair':'chair_01','shelf':'shelf_01'}, [('desk','north')],[('chair','desk')], (.1,.1,.85,.9), ['desk']),
    scene('living_001',6,5,{'sofa':'sofa_01','table':'table_01','shelf':'shelf_01'}, [('sofa','west')],[],(.1,.15,.9,.9),['sofa']),
    scene('kitchen_001',5.5,4,{'counter':'counter_01','cabinet':'cabinet_01','stool':'stool_01'}, [('counter','north')],[],(.1,.15,.9,.9),['counter']),
    scene('study_001',4.5,4,{'desk':'desk_01','chair':'chair_01','shelf':'shelf_01'}, [('desk','south')],[('chair','desk')],(.1,2.6,.9,.9),['desk']),
    scene('studio_001',4.8,4.2,{'bed':'bed_01','desk':'desk_01','chair':'chair_01'}, [('desk','north')],[('chair','desk')],(.1,.1,.85,.9),['desk']),
    scene('meeting_001',6.5,4.5,{'table':'table_01','chair':'chair_01','shelf':'shelf_01'}, [('shelf','east')],[('chair','table')],(.1,.15,.85,.9),['table']),
]
for spec in scenes:
    path=ROOT/'examples'/f'{spec["scene_id"]}.json'
    path.write_text(json.dumps(spec,indent=2),encoding='utf-8')
print('wrote',len(scenes),'specs')
