"""Simple, optional visualization exports without Blender or GPU dependencies.

OBJ geometry consists of proxy cuboids, not photorealistic asset meshes.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape
from .geometry import footprint
from .schema import SceneSpec, SceneState


COLORS = (("#487cba", "0.29 0.49 0.73"), ("#b66d56", "0.71 0.43 0.34"),
          ("#66a780", "0.40 0.65 0.50"), ("#d3a34c", "0.82 0.64 0.30"),
          ("#a582b8", "0.65 0.51 0.72"))


def export_svg(spec: SceneSpec, state: SceneState, filename: str | Path) -> None:
    scale, margin = 100.0, 42.0
    width = round(spec.room.width * scale + 2 * margin)
    height = round(spec.room.depth * scale + 2 * margin)
    body = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
            '<rect width="100%" height="100%" fill="#f5f6f9"/>',
            f'<rect x="{margin}" y="{margin}" width="{spec.room.width * scale}" height="{spec.room.depth * scale}" fill="#fff" stroke="#303a49" stroke-width="3"/>']
    for r in spec.regions:
        x = margin + r.x * scale
        y = margin + (spec.room.depth - r.y - r.depth) * scale
        body.append(f'<rect x="{x}" y="{y}" width="{r.width * scale}" height="{r.depth * scale}" fill="#d7f2df" stroke="#4a966b" stroke-dasharray="6 4"/>')
        body.append(f'<text x="{x+4}" y="{y+16}" font-size="13" fill="#286344">{escape(r.id)}</text>')
    for idx, obj in enumerate(state.objects):
        rect = footprint(obj, spec.assets[obj.asset])
        x = margin + rect[0] * scale
        y = margin + (spec.room.depth - rect[3]) * scale
        w, h = (rect[2] - rect[0]) * scale, (rect[3] - rect[1]) * scale
        body.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="4" fill="{COLORS[idx % len(COLORS)][0]}" fill-opacity=".8" stroke="#263343"/>')
        body.append(f'<text x="{x+w/2:.2f}" y="{y+h/2:.2f}" font-size="12" text-anchor="middle" dominant-baseline="middle" fill="white">{escape(obj.id)}</text>')
    body.append(f'<text x="{width/2}" y="26" font-size="18" text-anchor="middle" font-weight="600" fill="#253041">{escape(spec.scene_id)}</text>')
    body.append('</svg>')
    Path(filename).write_text('\n'.join(body), encoding='utf-8')


def export_obj(spec: SceneSpec, state: SceneState, filename: str | Path) -> None:
    path = Path(filename)
    mtl_path = path.with_suffix('.mtl')
    lines = [f'mtllib {mtl_path.name}']
    materials = []
    counter = 0
    for idx, obj in enumerate(state.objects):
        asset = spec.assets[obj.asset]
        x0, y0, x1, y1 = footprint(obj, asset)
        z0, z1 = 0.0, asset.height
        vertices = [(x0,y0,z0), (x1,y0,z0), (x1,y1,z0), (x0,y1,z0),
                    (x0,y0,z1), (x1,y0,z1), (x1,y1,z1), (x0,y1,z1)]
        lines.extend([f'o {obj.id}', f'usemtl material_{idx}'])
        for x,y,z in vertices:
            lines.append(f'v {x:.6f} {y:.6f} {z:.6f}')
        for face in ((1,4,3,2), (5,6,7,8), (1,2,6,5), (2,3,7,6), (3,4,8,7), (4,1,5,8)):
            lines.append('f ' + ' '.join(str(counter+n) for n in face))
        counter += 8
        materials.append(f'newmtl material_{idx}\nKd {COLORS[idx % len(COLORS)][1]}\nKa 0.1 0.1 0.1\n')
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    mtl_path.write_text('\n'.join(materials), encoding='utf-8')
