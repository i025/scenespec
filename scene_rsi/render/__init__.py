"""Optional visual export. Physics, scene contracts and scoring stay outside Blender."""
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import subprocess
import time

from ..evaluator.verifier import SceneVerifier

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / 'assets/world/library.blend'
ASSETS = ('agent', 'crate', 'obstacle')
# Room-relative, fixed across every scene; Z up, asset front +Y.
CAMERAS = {
    'overview': (1.15, -1.35, 1.35),
    'reverse': (-1.35, 1.15, 1.35),
    'top': (0, 0, 2.15),
}


def instances(spec, state):
    """Transforms for normalized, floor-centred library meshes (metres, Z up)."""
    result = []
    for obj in state.objects:
        if obj.asset not in ASSETS:
            raise ValueError(f'no fixed visual asset for {obj.asset!r}; available: {ASSETS}')
        asset = spec.assets[obj.asset]
        result.append({'id': obj.id, 'asset': obj.asset,
                       'location': [obj.x, obj.y, 0],
                       'rotation_z': math.radians(obj.yaw_deg),
                       'scale': [asset.width, asset.depth, asset.height]})
    return result


def export_scene(spec, state, out, blender='blender', device='auto', gpu=None,
                 size=768, samples=32, threads=4):
    """Run our trusted Blender script on JSON, never candidate Python code.

    auto tries one visible CUDA device and retries rendering on CPU if necessary.
    cpu forces CPU. cuda requires CUDA (useful for an explicit acceptance test).
    """
    placements = instances(spec, state)
    verification = SceneVerifier().verify(spec, state)
    payload = {'spec': asdict(spec), 'state': state.to_dict(),
               'instances': placements, 'cameras': CAMERAS}
    return export_payload(payload, verification, out, blender, device, gpu, size, samples, threads)


def export_spatial(case, response, out, **options):
    """Trusted 3D adapter; keep the existing floor-only export contract unchanged."""
    from ..benchmark.spatial_tasks import check, unpack
    from ..schema import SceneSpec
    verification = check(case, response)
    if not verification['success']:
        raise ValueError('only a verified spatial task result may be rendered')
    packet = case['packet']
    spec = SceneSpec.from_dict(packet['spec'])
    state, _ = unpack(response)
    placements = []
    for obj in state.objects:
        asset = spec.assets[obj.asset]
        placements.append({'id':obj.id, 'asset':packet['visual_assets'][obj.asset],
                           'location':[obj.x, obj.y, response['elevations'][obj.id]],
                           'rotation_z':math.radians(obj.yaw_deg),
                           'scale':[asset.width, asset.depth, asset.height]})
    payload = {'spec':packet['spec'], 'state':state.to_dict(), 'instances':placements,
               'cameras':CAMERAS, 'view':packet['view']}
    return export_payload(payload, verification, out, **options)


def export_payload(payload, verification, out, blender='blender', device='auto', gpu=None,
                   size=768, samples=32, threads=4):
    """Render already constructed trusted JSON using the existing asset pipeline."""
    if device not in ('auto', 'cpu', 'cuda'):
        raise ValueError('device must be auto, cpu or cuda')
    if not (64 <= size <= 4096 and 1 <= samples <= 1024 and 1 <= threads <= 32):
        raise ValueError('invalid render size, sample count or CPU thread limit')
    if not LIBRARY.is_file():
        raise FileNotFoundError('build assets first: see RENDERING.md')
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'scene.json').write_text(json.dumps(payload, indent=2) + '\n')
    command = [str(blender), '--background', '--factory-startup', '--disable-autoexec',
               '--threads', str(threads), '--python-exit-code', '1',
               '--python', str(Path(__file__).with_name('blender_scene.py')),
               '--', str(out / 'scene.json'), str(LIBRARY), str(out), device,
               str(size), str(samples)]
    env = os.environ.copy()
    if gpu is not None:
        env['CUDA_VISIBLE_DEVICES'] = str(gpu)
    if device == 'cpu':
        env['CUDA_VISIBLE_DEVICES'] = ''
    env['OMP_NUM_THREADS'] = str(threads)
    started = time.perf_counter()
    with (out / 'blender.log').open('w') as log:
        process = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
    result = {'scene_id': payload['state']['scene_id'], 'verification': verification,
              'exit_code': process.returncode, 'wall_seconds': time.perf_counter() - started,
              'requested_device': device, 'visible_gpu': env.get('CUDA_VISIBLE_DEVICES'),
              'size': size, 'samples': samples, 'threads': threads}
    if (out / 'render.json').exists():
        result['render'] = json.loads((out / 'render.json').read_text())
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    if process.returncode:
        raise RuntimeError(f'Blender failed; see {out / "blender.log"}')
    return result
