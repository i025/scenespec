"""Continuous task distribution. Split seeds and witnesses are private run inputs.

There are no pose domains. Fixed inclined mounts make height and roll/pitch
vary continuously with position and orientation on a supporting surface.
"""
from dataclasses import asdict
import math
import random

import numpy as np
import pybullet as p

from ..schema import Asset, RequiredObject, Room, SceneSpec
from .geometry import pose, rotation

FAMILIES = ('inclined','doorway','occlusion','paired')


def supported(task, name, u, v, yaw):
    spec = task['spec']
    required = {q['id']:q['asset'] for q in spec['required_objects']}
    a = spec['assets'][required[name]]
    parent = next(q for q in task['fixed'] if q['id'] == task['supports'][name])
    b = spec['assets'][parent['asset']]
    position = np.array(parent['position'])+rotation(parent['rotation'])@np.array([u,v,(b['height']+a['height'])/2+.003])
    _, quat = p.multiplyTransforms([0,0,0],p.getQuaternionFromEuler(parent['rotation']),[0,0,0],p.getQuaternionFromEuler([0,0,yaw]))
    return pose(name,required[name],position,p.getEulerFromQuaternion(quat))


def make_task(seed, family, name):
    rng = random.Random(seed)
    w,d = rng.uniform(6.8,8.8),rng.uniform(5.4,7.2)
    assets = {'bench':Asset(2.6,1.25,.55),'narrow_bench':Asset(2.,1.15,.55),
              'box':Asset(.6,.6,.6),'cabinet':Asset(1.1,.65,1.3),'robot':Asset(.6,.6,.9)}
    meshes = {'bench':'obstacle','narrow_bench':'obstacle','box':'crate','cabinet':'obstacle','robot':'agent'}
    fixed,required,supports = [],[],{}
    def mount(id_,asset,x,y,tilted=True):
        a = assets[asset]
        rpy = [rng.uniform(-.04,.04),rng.uniform(.065,.14),rng.uniform(-.24,.24)] if tilted else [0,0,0]
        r = rotation(rpy)
        half = np.array([a.width,a.depth,a.height])/2
        z = float(np.abs(r[2])@half)+.003
        fixed.append(pose(id_,asset,[x,y,z],rpy))
        required.append(RequiredObject(id_,asset))
    if family == 'paired':
        for i,x in enumerate((w*.27,w*.73)):
            mount('mount_'+str(i),'narrow_bench',x,d*.66)
            supports['box_'+str(i)] = 'mount_'+str(i)
    else:
        mount('mount','narrow_bench' if family == 'doorway' else 'bench',w*(.71 if family == 'doorway' else .60),d*.64)
        supports.update(box_0='mount',box_1='mount')
    if family == 'doorway':
        for i,(y0,y1) in enumerate(((0,d*.43-.65),(d*.43+.65,d))):
            asset = 'wall_'+str(i)
            assets[asset] = Asset(.18,y1-y0,1.15)
            meshes[asset] = 'obstacle'
            mount('divider_'+str(i),asset,w*.42,(y0+y1)/2,False)
    if family == 'occlusion':
        assets['screen'] = Asset(.7,.18,1.6)
        meshes['screen'] = 'obstacle'
        mount('screen','screen',w*.60,d*.64-1.0,False)
    for name_,asset in (('box_0','box'),('box_1','box'),('storage','cabinet'),('rover','robot')):
        required.append(RequiredObject(name_,asset))
    supports.update(storage='floor',rover='floor')
    spec = SceneSpec(name,Room(w,d,3.8),tuple(required),assets)
    task = {'family':family,'spec':asdict(spec),'meshes':meshes,'fixed':fixed,'supports':supports,
            'zones':{'storage':[.25,d-2.25,2.1,d-.25],'rover':[.35,.65,2.55,2.95]},
            'relations':[{'a':'storage','b':'rover','min':1.,'max':5.8,'target':2.2}],
            'camera':{'position':[w*.60,-1.5,3.2],'target':[w*.60,d*.62,.9],'fov':65.},
            'visibility':{'box_0':.55,'box_1':.55},'entry':[.5,.5],'goal':[w-.5,d-.5]}
    witnesses = list(fixed)
    for i in range(2):
        witnesses.append(supported(task,'box_'+str(i),0 if family == 'paired' else (-.67 if i == 0 else .67),-.06,(-.1 if i == 0 else .1)))
    witnesses += [pose('storage','cabinet',[1.12,d-1.1,.653]),pose('rover','robot',[1.35,1.65,.453])]
    if family == 'paired':
        task['zones'] = {'storage':[.25,.65,2.1,2.85],'rover':[w-2.4,.65,w-.35,2.95]}
        task['relations'][0].update(max=7.,target=4.3)
        witnesses[-2] = pose('storage','cabinet',[1.12,1.45,.653])
        witnesses[-1] = pose('rover','robot',[w-1.25,1.65,.453])
    return task,{'scene_id':name,'objects':witnesses}
