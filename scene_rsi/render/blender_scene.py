"""Trusted Blender entry point. Input is data only; no project evaluator imports."""
import json
import math
from pathlib import Path
import resource
import sys
import time

import bpy
from mathutils import Vector


def material(name, color, metallic=0, roughness=.5):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    node = mat.node_tree.nodes.get('Principled BSDF')
    node.inputs['Base Color'].default_value = (*color, 1)
    node.inputs['Metallic'].default_value = metallic
    node.inputs['Roughness'].default_value = roughness
    return mat


def box(name, position, size, mat, bevel=0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=position)
    obj = bpy.context.object
    obj.name, obj.scale = name, size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    if bevel:
        modifier = obj.modifiers.new('Soft edges', 'BEVEL')
        modifier.width, modifier.segments = bevel, 3
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    return obj


def aim(obj, point):
    obj.rotation_euler = (Vector(point) - obj.location).to_track_quat('-Z', 'Y').to_euler()


def build_scene(data, library):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    room = data['spec']['room']
    w, d = room['width'], room['depth']
    scene['scene_id'] = data['state']['scene_id']
    with bpy.data.libraries.load(str(library), link=False) as (_, loaded):
        loaded.objects = ['agent', 'crate', 'obstacle']
    assets = {obj.name: obj for obj in loaded.objects}
    for name, template in assets.items():
        template.name = 'Template_' + name
    for instance in data['instances']:
        obj = assets[instance['asset']].copy()
        scene.collection.objects.link(obj)
        obj.name = instance['id']
        obj['scene_object_id'] = instance['id']
        obj['scene_asset'] = instance['asset']
        obj.location = instance['location']
        obj.rotation_euler.z = instance['rotation_z']
        obj.scale = instance['scale']

    floor_mats = [material(f'Floor {i}', (.32+i*.012, .35+i*.012, .36+i*.012)) for i in range(3)]
    rim = material('Wall plaster', (.72, .74, .69))
    trim = material('Boundary trim', (.1, .16, .18), .35)
    green = material('Goal paint', (.1, .55, .27), .05, .55)
    pale = material('Goal border', (.58, .88, .32), .05, .45)
    backdrop = material('Studio', (.08, .115, .15))
    box('Floor foundation', (w/2, d/2, -.17), (w+.2, d+.2, .24), trim, .04)
    # Presentation floor has metre seams matching the existing discrete cells.
    for x in range(math.ceil(w)):
        for y in range(math.ceil(d)):
            sx, sy = min(1, w-x), min(1, d-y)
            box(f'Tile_{x}_{y}', (x+sx/2, y+sy/2, -.025), (sx-.014, sy-.014, .05),
                floor_mats[(x+2*y) % 3], .007)
    # All four walls are cut away at the same height so both oblique views are readable.
    for x in (-.09, w+.09):
        box('Cutaway wall', (x, d/2, .24), (.16, d+.34, .48), rim, .015)
        box('Wall coping', (x, d/2, .49), (.18, d+.36, .04), trim, .009)
    for y in (-.09, d+.09):
        box('Cutaway wall', (w/2, y, .24), (w+.02, .16, .48), rim, .015)
        box('Wall coping', (w/2, y, .49), (w+.04, .18, .04), trim, .009)
    for region in data['spec']['regions']:
        if region['id'] != 'goal':
            continue
        x, y, rw, rd = (region[k] for k in ('x', 'y', 'width', 'depth'))
        box('Goal area', (x+rw/2, y+rd/2, .004), (rw-.05, rd-.05, .008), green)
        for bx in (x+.06, x+rw-.06):
            box('Goal outline', (bx, y+rd/2, .01), (.025, rd-.1, .005), pale)
        for by in (y+.06, y+rd-.06):
            box('Goal outline', (x+rw/2, by, .01), (rw-.1, .025, .005), pale)
    box('Studio ground', (w/2, d/2, -.32), (200, 200, .1), backdrop)

    target = (w/2, d/2, .35)
    extent = max(w, d)
    for name, offset in data['cameras'].items():
        camera = bpy.data.cameras.new(name)
        obj = bpy.data.objects.new(name, camera)
        scene.collection.objects.link(obj)
        obj.location = Vector(target) + extent * Vector(offset)
        aim(obj, target)
        camera.type, camera.ortho_scale = 'ORTHO', extent*1.75
        camera.lens = 45
        camera.clip_end = 300
        if name == 'overview' and 'view' in data:
            # Same pinhole view as the deterministic visibility checker.
            view = data['view']
            obj.location = view['position']
            aim(obj, view['target'])
            camera.type = 'PERSP'
            camera.angle = math.radians(view['fov'])
    for name, position, energy, size, color in (
        ('Key softbox', (w*.2, -d*.5, 8), 1500, 5, (1, .86, .7)),
        ('Fill softbox', (w*1.3, d*.8, 6), 1100, 4, (.7, .84, 1)),
        ('Top softbox', (w*.5, d*1.1, 8), 1400, 4, (1, 1, 1)),
    ):
        light = bpy.data.lights.new(name, 'AREA')
        light.energy, light.shape, light.size, light.color = energy, 'DISK', size, color
        obj = bpy.data.objects.new(name, light)
        scene.collection.objects.link(obj)
        obj.location = position
        aim(obj, target)
    # Punctual sun is also represented in GLB's KHR_lights_punctual extension.
    light = bpy.data.lights.new('Daylight', 'SUN')
    light.energy, light.angle = .7, .3
    obj = bpy.data.objects.new('Daylight', light)
    scene.collection.objects.link(obj)
    obj.rotation_euler = (.35, -.45, -.5)
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (.32, .4, .5, 1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value = .35
    scene.camera = bpy.data.objects['overview']
    bpy.context.view_layer.update()
    return scene


def configure_device(scene, requested):
    scene.cycles.device = 'CPU'
    if requested == 'cpu':
        return 'CPU', 'CPU explicitly requested'
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'CUDA'
        prefs.refresh_devices()
        devices = [device for device in prefs.devices if device.type == 'CUDA']
        for device in prefs.devices:
            device.use = bool(devices and device == devices[0])
        if devices:
            scene.cycles.device = 'GPU'
            return 'CUDA', devices[0].name
        reason = 'No visible Cycles CUDA device'
    except Exception as error:
        reason = str(error)
    if requested == 'cuda':
        raise RuntimeError(reason)
    return 'CPU', reason


def main():
    source, library, destination, requested, size, samples = sys.argv[sys.argv.index('--')+1:]
    out = Path(destination)
    started, cpu_started = time.perf_counter(), time.process_time()
    data = json.loads(Path(source).read_text())
    scene = build_scene(data, Path(library))
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = int(samples)
    scene.cycles.use_denoising = True
    scene.render.resolution_x = scene.render.resolution_y = int(size)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.view_settings.view_transform = 'AgX'
    backend, device = configure_device(scene, requested)
    report = {'blender': bpy.app.version_string, 'backend': backend, 'device': device,
              'fallback_reason': None, 'views': {},
              'scene_objects': len(data['instances']),
              'meshes': sum(obj.type == 'MESH' for obj in scene.objects)}
    for name in data['cameras']:
        scene.camera = bpy.data.objects[name]
        scene.render.filepath = str(out / f'{name}.png')
        view_start = time.perf_counter()
        try:
            bpy.ops.render.render(write_still=True)
        except RuntimeError as error:
            if requested != 'auto' or scene.cycles.device == 'CPU':
                raise
            scene.cycles.device = 'CPU'
            report.update(backend='CPU', device='CPU', fallback_reason=str(error))
            bpy.ops.render.render(write_still=True)
        report['views'][name] = {'seconds': time.perf_counter()-view_start,
                                 'backend': report['backend']}
    scene.camera = bpy.data.objects['overview']
    # Store portable CPU default in .blend; all images above record their actual device.
    scene.cycles.device = 'CPU'
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(out / 'scene.blend'), compress=True)
    # Studio backdrop and area softboxes are Blender presentation only.
    # Excluding the large backdrop keeps GLB viewer framing at room scale.
    bpy.ops.object.select_all(action='DESELECT')
    for obj in scene.objects:
        if obj.name != 'Studio ground' and not (obj.type == 'LIGHT' and obj.data.type == 'AREA'):
            obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(out / 'scene.glb'), export_format='GLB', use_selection=True,
                              export_cameras=True, export_lights=True, export_animations=False,
                              export_extras=True, export_yup=True)
    report.update(wall_seconds=time.perf_counter()-started,
                  cpu_seconds=time.process_time()-cpu_started,
                  peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
    (out / 'render.json').write_text(json.dumps(report, indent=2) + '\n')
    print('RENDER_RESULT', json.dumps(report))


if __name__ == '__main__':
    main()
