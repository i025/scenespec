"""Blender-only round trip against ACTUAL mesh vertices and full xyz/Euler poses."""
import json
from pathlib import Path
import struct
import sys

import bpy
import numpy as np
from mathutils import Euler,Vector


def check_mesh(obj,instance,pose,asset,geometry):
    vertices = np.array(geometry[instance['asset']]['vertices'])*[asset['width'],asset['depth'],asset['height']]
    r = np.array(Euler(pose['rotation'],'XYZ').to_matrix())
    expected = vertices@r.T+pose['position']
    actual = np.array([list(obj.matrix_world@v.co) for v in obj.data.vertices])
    assert np.max(np.abs(np.r_[actual.min(axis=0),actual.max(axis=0)]-np.r_[expected.min(axis=0),expected.max(axis=0)])) < 2e-5,obj.name
    assert len(obj.data.vertices) > 1000 and len(obj.data.materials) >= 3,obj.name


def main():
    root,geometry_path,report = sys.argv[sys.argv.index('--')+1:]
    geometry = json.loads(Path(geometry_path).read_text())
    results = []
    for path in sorted(Path(root).glob('*/scene.json')):
        directory = path.parent
        data = json.loads(path.read_text())
        poses = {q['id']:q for q in data['state']['objects']}
        bpy.ops.wm.open_mainfile(filepath=str(directory/'scene.blend'))
        bpy.context.view_layer.update()
        for instance in data['instances']:
            q = poses[instance['id']]
            check_mesh(bpy.data.objects[q['id']],instance,q,data['spec']['assets'][q['asset']],geometry)
        camera = bpy.data.objects['overview']
        np.testing.assert_allclose(list(camera.location),data['view']['position'],atol=1e-6)
        assert abs(np.degrees(camera.data.angle)-data['view']['fov']) < 1e-4
        assert sum(o.type=='CAMERA' for o in bpy.context.scene.objects) == 3
        blob = (directory/'scene.glb').read_bytes()
        magic,version,length = struct.unpack_from('<4sII',blob)
        assert (magic,version,length)==(b'glTF',2,len(blob))
        size,kind = struct.unpack_from('<II',blob,12)
        gltf = json.loads(blob[20:20+size])
        assert len(gltf['cameras'])==3 and 'KHR_lights_punctual' in gltf['extensionsUsed']
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(directory/'scene.glb'))
        bpy.context.view_layer.update()
        for instance in data['instances']:
            q = poses[instance['id']]
            obj = next(o for o in bpy.context.scene.objects if o.get('scene_object_id')==q['id'])
            check_mesh(obj,instance,q,data['spec']['assets'][q['asset']],geometry)
        results.append({'directory':directory.name,'objects':len(poses),'blend_and_glb_actual_mesh_bounds':True,
                        'full_xyz_euler_preserved':True,'fixed_camera_preserved':True,'three_cameras':True})
    assert len(results)>=4
    Path(report).write_text(json.dumps(results,indent=2)+'\n')
    print('CONTINUOUS_EXPORT_ACCEPTANCE',json.dumps(results))


if __name__ == '__main__':
    main()
