"""Blender-only authoring script: --background --factory-startup --python THIS -- library.blend.

Own meshes, no downloaded content. All bevels applied before saving the fixed library.
"""
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def material(name, color, metallic=0, roughness=.45):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (*color, 1)
    bsdf.inputs['Metallic'].default_value = metallic
    bsdf.inputs['Roughness'].default_value = roughness
    return mat


def finish(obj, name, mat, bevel=0):
    obj.name = name
    obj.data.materials.append(mat)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        modifier = obj.modifiers.new('Edge highlights', 'BEVEL')
        modifier.width, modifier.segments = bevel, 3
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        modifier = obj.modifiers.new('Weighted normals', 'WEIGHTED_NORMAL')
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    return obj


def box(name, center, size, mat, bevel=.008):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.scale = size
    return finish(obj, name, mat, bevel)


def cylinder(name, center, radius, depth, mat, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=radius, depth=depth,
                                      location=center, rotation=rotation)
    return finish(bpy.context.object, name, mat, .006)


def join_asset(name, parts):
    bpy.ops.object.select_all(action='DESELECT')
    for part in parts:
        part.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    obj = bpy.context.object
    obj.name = name
    bpy.context.scene.cursor.location = (0, 0, 0)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    # Normalize the authored envelope, preserving each part's relative position.
    coords = [v.co.copy() for v in obj.data.vertices]
    lo = Vector(tuple(min(v[i] for v in coords) for i in range(3)))
    hi = Vector(tuple(max(v[i] for v in coords) for i in range(3)))
    for vertex in obj.data.vertices:
        vertex.co = Vector(tuple((vertex.co[i] - lo[i]) / (hi[i] - lo[i])
                                 - (.5 if i < 2 else 0) for i in range(3)))
    obj.data.update()
    return obj


def build(destination):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    wood = [material(f'Timber {i}', color) for i, color in enumerate(
        ((.36, .17, .055), (.48, .25, .09), (.58, .33, .14), (.42, .21, .075)))]
    edge = material('Crate battens', (.65, .39, .18))
    steel = material('Brushed steel', (.32, .39, .42), .75, .3)
    dark = material('Rubber and recesses', (.018, .025, .032), .05, .7)
    blue = material('Agent enamel', (.025, .25, .58), .35, .28)
    white = material('Warm white enamel', (.8, .84, .82), .25, .3)
    teal = material('Cabinet enamel', (.11, .22, .24), .4, .35)
    amber = material('Agent amber marker', (1, .37, .025), .15, .25)

    parts = [box('Crate core', (0, 0, .5), (.88, .88, .88), wood[0])]
    for side in (-1, 1):
        for index in range(5):
            pos = -.36 + index * .18
            parts += [box('Vertical plank', (pos, side*.46, .5), (.174, .04, .86), wood[index % 4]),
                      box('Side plank', (side*.46, pos, .5), (.04, .174, .86), wood[(index+1) % 4]),
                      box('Top plank', (pos, 0, .95 if side == 1 else .05), (.174, .9, .04), wood[index % 4])]
        for z in (.11, .89):
            parts += [box('Front batten', (0, side*.49, z), (1, .02, .14), edge),
                      box('Side batten', (side*.49, 0, z), (.02, 1, .14), edge)]
        for x in (-.42, .42):
            parts.append(box('Upright', (x, side*.49, .5), (.12, .02, .7), edge))
            for z in (.11, .89):
                parts.append(cylinder('Fastener', (x, side*.505, z), .018, .012, steel,
                                      (math.pi/2, 0, 0)))
    join_asset('crate', parts)

    parts = [box('Chassis', (0, 0, .24), (.76, .78, .23), dark, .035),
             box('Blue body', (0, 0, .48), (.8, .75, .38), blue, .055),
             box('White lid', (0, 0, .7), (.76, .72, .12), white, .03),
             box('Front bumper', (0, .41, .25), (.76, .12, .16), dark, .025),
             box('Front marker', (0, .4, .51), (.42, .02, .045), amber),
             cylinder('Lidar stem', (0, -.07, .82), .07, .15, steel),
             cylinder('Lidar', (0, -.07, .93), .18, .14, dark)]
    for side in (-1, 1):
        for y in (-.25, .25):
            parts += [cylinder('Wheel', (side*.43, y, .18), .18, .14, dark, (0, math.pi/2, 0)),
                      cylinder('Wheel hub', (side*.502, y, .18), .09, .008, steel, (0, math.pi/2, 0))]
        parts.append(cylinder('Front sensor', (side*.2, .392, .6), .04, .025,
                              dark, (math.pi/2, 0, 0)))
    join_asset('agent', parts)

    parts = [box('Closed cabinet', (0, 0, .5), (.96, .92, .92), teal, .02),
             box('Plinth', (0, 0, .035), (.9, .88, .07), dark),
             box('Cap', (0, 0, .98), (1, 1, .04), steel)]
    for x in (-.235, .235):
        parts.append(box('Door', (x, .475, .53), (.45, .04, .84), teal, .012))
        for z in (.22, .25, .28, .77, .8, .83):
            parts.append(box('Vent', (x, .498, z), (.27, .004, .012), dark, .002))
        parts += [box('Handle mount', (x*.25, .501, .51), (.04, .02, .16), dark),
                  box('Handle', (x*.25, .524, .51), (.018, .024, .12), steel),
                  box('Label', (x, .5, .67), (.16, .006, .065), white)]
    join_asset('obstacle', parts)
    bpy.context.scene.world.color = (.15, .15, .15)
    bpy.context.preferences.filepaths.save_version = 0
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(destination), compress=True)
    print('FIXED_ASSETS', [(o.name, len(o.data.vertices)) for o in bpy.data.objects])


if __name__ == '__main__':
    build(sys.argv[sys.argv.index('--') + 1])
