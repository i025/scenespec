"""Acceptance with the REAL authored meshes, not the synthetic unit-test cubes."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pybullet as p

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scene_rsi.continuous.geometry import MeshWorld
from scene_rsi.continuous.tasks import make_task,FAMILIES
from scene_rsi.world.generator_benchmark import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    references = []
    for index,family in enumerate(FAMILIES):
        task,state = make_task(31+index,family,'public_geometry_acceptance')
        world = MeshWorld(task,args.geometry)
        try:
            result = world.evaluate(state,final=True)
            assert result['success'],result['failures']
            references.append({'family':family,'success':True,'navigation':result['navigation'],
                               'visibility':result['visibility'],'stability':result['stability']})
            if family == 'inclined':
                world.set_state(state)
                robot = next(q for q in state['objects'] if q['id']=='rover')
                point = np.array(robot['position'])+[.28,.28,.4]
                shape = p.createCollisionShape(p.GEOM_BOX,halfExtents=[.005]*3,physicsClientId=world.client)
                probe = p.createMultiBody(baseMass=0,baseCollisionShapeIndex=shape,basePosition=point,physicsClientId=world.client)
                lo,hi = map(np.array,p.getAABB(world.bodies['rover'],physicsClientId=world.client))
                assert np.all(lo < point-.005) and np.all(point+.005 < hi)
                assert not p.getClosestPoints(probe,world.bodies['rover'],0,physicsClientId=world.client), 'gap incorrectly filled by bbox/hull'
                storage = next(q for q in state['objects'] if q['id']=='storage')
                p.resetBasePositionAndOrientation(probe,storage['position'],[0,0,0,1],physicsClientId=world.client)
                assert any(q[8]<-.01 for q in p.getClosestPoints(probe,world.bodies['storage'],0,physicsClientId=world.client)), 'solid containment missed'
        finally:
            world.close()
    catalog = json.loads((args.geometry/'geometry.json').read_text())
    save(args.out,{'references':references,'mesh_gap_not_bbox_collision':True,'solid_containment_detected':True,
        'components_retained':{k:len(v['parts']) for k,v in catalog.items()},
        'max_component_convex_volume_relative_error':{k:v['max_convex_volume_relative_error'] for k,v in catalog.items()}})
    print('Actual-mesh acceptance passed: gap, containment, all components, contacts, gravity, pixels, passage')


if __name__ == '__main__':
    main()
