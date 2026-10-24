"""Contract tests need no Blender. Real mesh round trips have a separate acceptance command."""
from dataclasses import replace
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scene_rsi.evaluator.verifier import SceneVerifier
from scene_rsi.render import ASSETS, CAMERAS, export_scene, instances
from scene_rsi.schema import SceneState
from scene_rsi.world.planning import solve
from scene_rsi.world.task import GeneratorConfig, generate, make_task


class TestRenderContract(unittest.TestCase):
    def setUp(self):
        self.task = make_task(5, 5, {(3, 3)}, (1, 1), (2, 1), (3, 1))

    def test_exact_floor_centre_scale_and_positive_yaw(self):
        original = self.task.initial.objects[0]
        state = SceneState(self.task.spec.scene_id, (replace(original, yaw_deg=90),))
        transform = instances(self.task.spec, state)[0]
        self.assertEqual(transform['location'], [1.5, 1.5, 0])
        self.assertEqual(transform['scale'], [.56, .56, .9])
        self.assertAlmostEqual(transform['rotation_z'], math.pi/2)
        self.assertEqual(transform['id'], 'agent')

    def test_current_state_can_be_exported_separately_from_initial(self):
        state = replace(self.task.initial, objects=tuple(
            replace(obj, x=3.5) if obj.id == 'crate' else obj
            for obj in self.task.initial.objects))
        transforms = {item['id']: item for item in instances(self.task.spec, state)}
        self.assertEqual(transforms['crate']['location'], [3.5, 1.5, 0])
        self.assertEqual(self.task.initial.objects[1].x, 2.5)

    def test_visual_adapter_does_not_change_contract_or_solvability(self):
        task = generate(33, GeneratorConfig(5, 7, 5, require_solvable=True))
        before = task.to_dict()
        geometry = SceneVerifier().verify(task.spec, task.initial)
        solvability = solve(task)
        instances(task.spec, task.initial)
        self.assertEqual(task.to_dict(), before)
        self.assertEqual(SceneVerifier().verify(task.spec, task.initial), geometry)
        self.assertEqual(solve(task), solvability)

    def test_fixed_views_and_asset_catalog(self):
        self.assertEqual(set(CAMERAS), {'overview', 'reverse', 'top'})
        self.assertEqual(set(ASSETS), {'agent', 'crate', 'obstacle'})
        self.assertTrue(all(len(position) == 3 for position in CAMERAS.values()))

    def test_unknown_visual_asset_is_explicitly_unsupported(self):
        state = replace(self.task.initial, objects=(replace(self.task.initial.objects[0], asset='bed'),))
        with self.assertRaisesRegex(ValueError, 'no fixed visual asset'):
            instances(self.task.spec, state)

    def test_cpu_hides_gpu_and_export_uses_only_trusted_script_and_json(self):
        def fake_blender(command, **kwargs):
            self.assertIn('--disable-autoexec', command)
            self.assertIn('--factory-startup', command)
            self.assertEqual(kwargs['env']['CUDA_VISIBLE_DEVICES'], '')
            self.assertTrue(command[command.index('--python')+1].endswith('render/blender_scene.py'))
            source = Path(command[command.index('--')+1])
            payload = json.loads(source.read_text())
            self.assertEqual(payload['state'], self.task.initial.to_dict())
            self.assertNotIn('reference_actions', payload)
            return type('Result', (), {'returncode': 0})()
        with TemporaryDirectory() as temp, patch('scene_rsi.render.subprocess.run', fake_blender):
            report = export_scene(self.task.spec, self.task.initial, Path(temp)/'export', device='cpu')
            self.assertTrue(report['verification']['valid'])
            self.assertEqual(report['exit_code'], 0)

    def test_render_failure_records_failure_without_changing_evaluation(self):
        with TemporaryDirectory() as temp, patch('scene_rsi.render.subprocess.run') as run:
            run.return_value.returncode = 1
            out = Path(temp)/'export'
            with self.assertRaisesRegex(RuntimeError, 'Blender failed'):
                export_scene(self.task.spec, self.task.initial, out)
            self.assertEqual(json.loads((out/'result.json').read_text())['exit_code'], 1)
            self.assertTrue(SceneVerifier().verify(self.task.spec, self.task.initial)['valid'])


if __name__ == '__main__':
    unittest.main()
