"""Paired statistics and public DEV evidence; hidden packets/seeds stay private."""
import argparse
import itertools
import json
import math
from pathlib import Path
import random
import shutil
import sys

import numpy as np
import pybullet as p

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scene_rsi.continuous.baselines import METHODS
from scene_rsi.continuous.geometry import MeshWorld
from scene_rsi.world.generator_benchmark import save


def interval(values):
    rng = random.Random(20261025)  # Analysis resampling only, never task generation.
    samples = [sum(rng.choice(values) for _ in values)/len(values) for _ in range(10000)]
    return {'tasks':len(values),'mean':float(np.mean(values)),
            'task_cluster_bootstrap_95':np.quantile(samples,[.025,.975]).tolist()}


def difference(rows,a,b,extract):
    return interval([np.mean([extract(r) for r in methods[a]])-np.mean([extract(r) for r in methods[b]]) for methods in rows])


def distance(task,a,b):
    pa,pb = [{q['id']:q for q in state['objects']} for state in (a,b)]
    groups = {}
    for name,parent in task['supports'].items():
        groups.setdefault((pa[name]['asset'],parent),[]).append(name)
    pairs = []
    for names in groups.values():
        target = min(itertools.permutations(names),key=lambda ids:sum(math.dist(pa[x]['position'],pb[y]['position'])**2 for x,y in zip(names,ids)))
        pairs.extend(zip(names,target))
    rms = math.sqrt(sum(math.dist(pa[x]['position'],pb[y]['position'])**2 for x,y in pairs)/len(pairs))
    angles = []
    for x,y in pairs:
        qa,qb = [p.getQuaternionFromEuler(obj['rotation']) for obj in (pa[x],pb[y])]
        angles.append(2*math.acos(min(1.,abs(sum(u*v for u,v in zip(qa,qb))))))
    room = task['spec']['room']
    return {'position_rms_m':rms,'position_rms_room_normalized':rms/math.hypot(room['width'],room['depth']),
            'rotation_mean_deg':float(np.degrees(np.mean(angles)))}


def task_packets(value):
    if isinstance(value,dict):
        if {'spec','supports','camera'} <= value.keys():
            yield value
        for child in value.values():
            yield from task_packets(child)
    elif isinstance(value,list):
        for child in value:
            yield from task_packets(child)


def integers(value):
    if type(value) is int:
        yield value
    elif isinstance(value,dict):
        for child in value.values():
            yield from integers(child)
    elif isinstance(value,list):
        for child in value:
            yield from integers(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--geometry',type=Path,required=True)
    parser.add_argument('--replay',action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    cases = json.loads((args.run/'private_tasks.json').read_text())
    for split in ('dev','val','test'):
        shutil.copyfile(args.run/split/'summary.json',args.out/(split+'_summary.json'))
    dev = args.out/'dev'
    dev.mkdir()
    for index,case in enumerate(cases['dev']):
        save(dev/f'task_{index:02d}.json',case['task'])
        for method in METHODS:
            shutil.copyfile(args.run/'dev'/f'{index:02d}_0_{method}.json',dev/f'{index:02d}_0_{method}.json')
    save(dev/'algorithm_seeds.json',[c['algorithm_seeds'][0] for c in cases['dev']])
    rows = []
    diversity = {method:{'all':[],'valid_pairs':[]} for method in METHODS}
    for index,case in enumerate(cases['test']):
        methods = {method:[json.loads((args.run/'test'/f'{index:02d}_{repeat}_{method}.json').read_text()) for repeat in (0,1)] for method in METHODS}
        rows.append(methods)
        for method,results in methods.items():
            delta = distance(case['task'],*[r['state'] for r in results])
            diversity[method]['all'].append(delta)
            if all(r['result']['success'] for r in results):
                diversity[method]['valid_pairs'].append(delta)
    comparisons = {
        'repair_minus_random_hard_success':difference(rows,'repair','random',lambda r:r['result']['success']),
        'joint_minus_repair_hard_success':difference(rows,'joint','repair',lambda r:r['result']['success']),
        'repair_minus_joint_preference_error_m':difference(rows,'repair','joint',lambda r:r['result']['quality']['preference_error_m']),
        'joint_minus_repair_visibility':difference(rows,'joint','repair',lambda r:r['result']['quality']['visibility_mean'])}
    hard = comparisons['repair_minus_random_hard_success']
    preferred = comparisons['repair_minus_joint_preference_error_m']
    visible = comparisons['joint_minus_repair_visibility']
    joint_hard = comparisons['joint_minus_repair_hard_success']
    save(args.out/'paired_comparisons.json',{'comparisons':comparisons,'analysis_unit':'12 tasks; two seeds averaged within task',
        'predeclared_discrimination_pass':bool(hard['task_cluster_bootstrap_95'][0]>0 or
            preferred['mean']>=.1 and preferred['task_cluster_bootstrap_95'][0]>0 and visible['mean']>=-.05 and joint_hard['mean']>=-.05)})
    save(args.out/'diversity.json',{method:{label:{'task_pairs':len(values),
         **{key:float(np.mean([v[key] for v in values])) if values else None
            for key in ('position_rms_m','position_rms_room_normalized','rotation_mean_deg')}} for label,values in samples.items()}
         for method,samples in diversity.items()})
    private = [case['task'] for split in ('val','test') for case in cases[split]]
    files = list(args.out.rglob('*.json'))
    if (args.out.parent/'scenes').is_dir():
        files += list((args.out.parent/'scenes').rglob('*.json'))
    documents = [json.loads(file.read_text()) for file in files]
    public = [packet for value in documents for packet in task_packets(value)]
    assert not any(a==b for a in public for b in private)
    private_seeds = [value for split in ('val','test') for case in cases[split]
                     for value in [case['seed'],*case['algorithm_seeds']]]
    assert not any(value in private_seeds for document in documents for value in integers(document))
    save(args.out/'privacy.json',{'private_task_count':len(private),'public_packet_occurrences':len(public),
                                'private_packets_found':0,'hidden_seed_matches':0,'heldout_seeds_published':False,
                                'method':'direct structured value/seed comparison, no hashes'})
    if args.replay:
        count,error = 0,0.
        for split in ('dev','val','test'):
            for index,case in enumerate(cases[split]):
                for repeat in range(2 if split=='test' else 1):
                    for method in METHODS:
                        row = json.loads((args.run/split/f'{index:02d}_{repeat}_{method}.json').read_text())
                        world = MeshWorld(case['task'],args.geometry)
                        try:
                            result = world.evaluate(row['state'],final=True)
                        finally:
                            world.close()
                        assert result['success']==row['result']['success'] and result['failures']==row['result']['failures']
                        for key,value in result['quality'].items():
                            error = max(error,abs(value-row['result']['quality'][key]))
                        assert error<1e-9
                        count += 1
        save(args.out/'score_replay.json',{'states_rescored':count,'hard_results_match':True,'max_quality_error':error,
                                         'model_calls':0,'generation_algorithm_calls':0})
    print(json.dumps(comparisons,indent=2))


if __name__ == '__main__':
    main()
