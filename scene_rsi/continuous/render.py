"""Reuse the existing real-asset Blender exporter for continuous six-DoF poses."""
import numpy as np

from ..render import CAMERAS, export_payload
from .geometry import MeshWorld, rotation


def export(task,state,geometry,out,**options):
    world = MeshWorld(task,geometry)
    try:
        verification = world.evaluate(state,final=True)
    finally:
        world.close()
    placements = []
    for obj in state['objects']:
        a = task['spec']['assets'][obj['asset']]
        # Library origin is bottom centre; new positions mean bbox centre.
        origin = np.array(obj['position'])-rotation(obj['rotation'])@np.array([0,0,a['height']/2])
        placements.append({'id':obj['id'],'asset':task['meshes'][obj['asset']],
                           'location':origin.tolist(),'rotation':obj['rotation'],
                           'scale':[a['width'],a['depth'],a['height']]})
    payload = {'spec':task['spec'],'state':state,'instances':placements,
               'cameras':CAMERAS,'view':task['camera']}
    return export_payload(payload,verification,out,**options)
