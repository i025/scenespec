"""Real Blender acceptance: reopen .blend and reimport GLB, compare meshes with source JSON.

blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python THIS -- EXAMPLES REPORT
"""
import json
import math
from pathlib import Path
import struct
import sys

import bpy
from mathutils import Vector


def check_bounds(obj, instance):
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    lower = [min(corner[i] for corner in corners) for i in range(3)]
    upper = [max(corner[i] for corner in corners) for i in range(3)]
    w, d, h = instance['scale']
    yaw = instance['rotation_z']
    dx = abs(math.cos(yaw))*w + abs(math.sin(yaw))*d
    dy = abs(math.sin(yaw))*w + abs(math.cos(yaw))*d
    x, y, z = instance['location']
    expected = [x-dx/2, y-dy/2, z, x+dx/2, y+dy/2, z+h]
    assert all(abs(a-b) < 1e-5 for a, b in zip(lower+upper, expected)), (obj.name, lower, upper, expected)
    assert obj.type == 'MESH' and len(obj.data.vertices) > 1000, obj.name
    assert len(obj.data.materials) >= 3, obj.name


def check(directory):
    data = json.loads((directory/'scene.json').read_text())
    bpy.ops.wm.open_mainfile(filepath=str(directory/'scene.blend'))
    bpy.context.view_layer.update()
    for instance in data['instances']:
        check_bounds(bpy.data.objects[instance['id']], instance)
    for name in data['cameras']:
        assert bpy.data.objects[name].type == 'CAMERA'
    assert len([obj for obj in bpy.context.scene.objects if obj.type == 'CAMERA']) == 3
    assert any(obj.type == 'LIGHT' for obj in bpy.context.scene.objects)
    binary = (directory/'scene.glb').read_bytes()
    magic, version, length = struct.unpack_from('<4sII', binary)
    assert (magic, version, length) == (b'glTF', 2, len(binary))
    json_length, chunk_type = struct.unpack_from('<II', binary, 12)
    assert chunk_type == 0x4e4f534a
    gltf = json.loads(binary[20:20+json_length])
    assert len(gltf['cameras']) == 3
    assert len(gltf['materials']) >= 10
    assert all('uri' not in buffer for buffer in gltf['buffers'])
    assert 'KHR_lights_punctual' in gltf['extensionsUsed']
    assert not any(node.get('name') == 'Studio ground' for node in gltf['nodes'])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(directory/'scene.glb'))
    bpy.context.view_layer.update()
    for instance in data['instances']:
        obj = next(obj for obj in bpy.context.scene.objects
                   if obj.get('scene_object_id') == instance['id'])
        check_bounds(obj, instance)
    return {'scene_id': data['state']['scene_id'], 'blend_reopened': True,
            'glb_reimported': True, 'asset_envelopes_match_state': True,
            'three_cameras': True, 'embedded_materials': len(gltf['materials']),
            'punctual_light_exported': True, 'objects_checked': len(data['instances'])}


def main():
    root, report = sys.argv[sys.argv.index('--')+1:]
    results = [check(directory) for directory in sorted(Path(root).resolve().glob('seed_*'))]
    assert len(results) >= 3
    Path(report).write_text(json.dumps(results, indent=2) + '\n')
    print('BLENDER_ACCEPTANCE', json.dumps(results))


if __name__ == '__main__':
    main()
