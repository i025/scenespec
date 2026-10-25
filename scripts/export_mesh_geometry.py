"""Trusted Blender export of the existing closed convex mesh components.

blender -b --factory-startup --python THIS -- library.blend OUTPUT
No convex hull approximation is admitted: component/hull volumes must agree.
"""
import json
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils import Vector


def main():
    library, destination = sys.argv[sys.argv.index('--')+1:]
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=False)
    with bpy.data.libraries.load(library, link=False) as (_, loaded):
        loaded.objects = ['agent','crate','obstacle']
    catalog = {}
    for obj in loaded.objects:
        mesh = obj.data
        mesh.calc_loop_triangles()
        vertices = [tuple(v.co-Vector((0,0,.5))) for v in mesh.vertices]
        triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
        adjacent = [set() for _ in vertices]
        for edge in mesh.edges:
            a, b = edge.vertices
            adjacent[a].add(b)
            adjacent[b].add(a)
        remaining, parts, mass, moment = set(range(len(vertices))), [], 0., Vector((0,0,0))
        errors = []
        while remaining:
            pending, component = [min(remaining)], set()
            while pending:
                index = pending.pop()
                if index in component:
                    continue
                component.add(index)
                pending.extend(adjacent[index]-component)
            remaining -= component
            indices = sorted(component)
            mapping = {v:i for i, v in enumerate(indices)}
            points = [vertices[i] for i in indices]
            faces = [tuple(mapping[i] for i in tri) for tri in triangles if tri[0] in component]
            bm = bmesh.new()
            vs = [bm.verts.new(p) for p in points]
            for face in faces:
                bm.faces.new([vs[i] for i in face])
            if any(len(e.link_faces) != 2 for e in bm.edges):
                raise ValueError('non-closed component: '+obj.name)
            volume = bm.calc_volume()
            hull = bmesh.new()
            for point in points:
                hull.verts.new(point)
            bmesh.ops.convex_hull(hull, input=list(hull.verts))
            hull_volume = hull.calc_volume()
            error = abs(hull_volume-volume)/volume
            if error > .001:
                raise ValueError(('nonconvex component', obj.name, error))
            errors.append(error)
            bm.free()
            hull.free()
            signed, centroid = 0., Vector((0,0,0))
            for i, j, k in faces:
                a, b, c = (Vector(points[t]) for t in (i,j,k))
                v = a.dot(b.cross(c))/6
                signed += v
                centroid += v*(a+b+c)/4
            centroid /= signed
            mass += volume
            moment += centroid*volume
            filename = f'{obj.name}_{len(parts)}.obj'
            write_obj(out/filename, points, faces)
            parts.append(filename)
        write_obj(out/(obj.name+'.obj'), vertices, triangles)
        catalog[obj.name] = {'vertices':vertices, 'triangles':triangles, 'parts':parts,
                             'center_of_mass':list(moment/mass), 'component_volume_sum':mass,
                             'max_convex_volume_relative_error':max(errors),
                             'mass_model':'uniform density per authored closed component, additive at overlaps'}
    (out/'geometry.json').write_text(json.dumps(catalog)+'\n')
    print('MESH_COMPONENTS', {k:len(v['parts']) for k,v in catalog.items()})


def write_obj(path, vertices, faces):
    path.write_text(''.join('v '+' '.join(map(str,v))+'\n' for v in vertices)+
                    ''.join('f '+' '.join(str(i+1) for i in face)+'\n' for face in faces))


if __name__ == '__main__':
    main()
