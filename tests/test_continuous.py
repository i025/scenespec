import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scene_rsi.continuous.baselines import generate
from scene_rsi.continuous.geometry import MeshWorld
from scene_rsi.continuous.tasks import make_task,supported


class ContinuousTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Small closed convex fixtures keep unit tests independent of Blender.
        # Real library components have a separate actual-mesh acceptance command.
        cls.temp = TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        vertices = [[x,y,z] for x in (-.5,.5) for y in (-.5,.5) for z in (-.5,.5)]
        faces = [(0,1,3),(0,3,2),(4,6,7),(4,7,5),(0,4,5),(0,5,1),
                 (2,3,7),(2,7,6),(0,2,6),(0,6,4),(1,5,7),(1,7,3)]
        obj = ''.join('v '+' '.join(map(str,v))+'\n' for v in vertices)+''.join('f '+' '.join(str(i+1) for i in t)+'\n' for t in faces)
        catalog = {}
        for name in ('agent','crate','obstacle'):
            (cls.directory/(name+'.obj')).write_text(obj)
            catalog[name] = {'vertices':vertices,'triangles':faces,'parts':[name+'.obj'],'center_of_mass':[0,0,0]}
        (cls.directory/'geometry.json').write_text(json.dumps(catalog))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.task,self.state = make_task(17,'inclined','unit')
        self.world = MeshWorld(self.task,self.directory)
        self.addCleanup(self.world.close)

    def test_reference_contacts_and_gravity(self):
        result = self.world.evaluate(self.state,final=True)
        self.assertTrue(result['success'],result['failures'])
        self.assertTrue(all(v['support_contact'] for v in result['stability'].values()))

    def test_surface_position_changes_height_and_full_rotation(self):
        a = supported(self.task,'box_0',.123,.111,.173)
        b = supported(self.task,'box_0',.124,.111,.173)
        self.assertNotEqual(a['position'][2],b['position'][2])
        self.assertGreater(abs(a['rotation'][0])+abs(a['rotation'][1]),.01)
        self.assertNotIn('domains',self.task)

    def test_required_objects_cannot_be_deleted(self):
        changed = copy.deepcopy(self.state)
        changed['objects'].pop()
        with self.assertRaises(ValueError):
            self.world.evaluate(changed)

    def test_asset_scale_and_camera_overrides_are_rejected(self):
        changed = copy.deepcopy(self.state)
        changed['objects'][-1]['scale'] = [.1,.1,.1]
        with self.assertRaises(ValueError):
            self.world.evaluate(changed)
        changed = copy.deepcopy(self.state)
        changed['camera'] = self.task['camera']
        with self.assertRaises(ValueError):
            self.world.evaluate(changed)

    def test_fixed_mount_is_immutable(self):
        changed = copy.deepcopy(self.state)
        changed['objects'][0]['position'][0] += .01
        with self.assertRaises(ValueError):
            self.world.evaluate(changed)

    def test_collision_includes_containment(self):
        changed = copy.deepcopy(self.state)
        a,b = [next(q for q in changed['objects'] if q['id'] == name) for name in ('box_0','box_1')]
        b.update(position=list(a['position']),rotation=list(a['rotation']))
        self.assertIn('mesh_collision',[f['type'] for f in self.world.evaluate(changed)['failures']])

    def test_floating_object_fails_contact_and_stability(self):
        changed = copy.deepcopy(self.state)
        next(q for q in changed['objects'] if q['id'] == 'box_0')['position'][2] += .12
        result = self.world.evaluate(changed,final=True)
        self.assertIn('support_contact',[f['type'] for f in result['failures']])
        self.assertIn('physical_stability',[f['type'] for f in result['failures']])

    def test_out_of_frame_is_not_visibility_success(self):
        changed = copy.deepcopy(self.state)
        next(q for q in changed['objects'] if q['id'] == 'box_0')['position'][0] = 40
        result = self.world.evaluate(changed)
        self.assertIn('camera_framing',[f['type'] for f in result['failures']])
        self.assertEqual(result['visibility']['box_0']['fraction'],0)

    def test_pixel_occlusion_uses_the_other_mesh(self):
        self.world.set_state(self.state)
        base = self.world.visibility()['box_0']['fraction']
        changed = copy.deepcopy(self.state)
        target = next(q for q in changed['objects'] if q['id'] == 'box_0')
        blocker = next(q for q in changed['objects'] if q['id'] == 'storage')
        camera = self.task['camera']['position']
        blocker['position'] = [(a+b)/2 for a,b in zip(camera,target['position'])]
        result = self.world.evaluate(changed)
        self.assertLess(result['visibility']['box_0']['fraction'],base)

    def test_real_rotated_vertex_bounds(self):
        changed = copy.deepcopy(self.state)
        obj = next(q for q in changed['objects'] if q['id'] == 'storage')
        obj['position'][0] = .1
        obj['rotation'] = [.123,.234,.456]
        self.assertIn('mesh_bounds',[f['type'] for f in self.world.evaluate(changed)['failures']])

    def test_equal_query_ceiling_and_deterministic_output(self):
        for method in ('random','repair','joint'):
            a,cost = generate(self.task,self.world,method,123,budget=4)
            b,_ = generate(self.task,self.world,method,123,budget=4)
            self.assertEqual(cost['queries'],4)
            self.assertEqual(a,b)

    def test_render_adapter_preserves_full_pose_and_dimensions(self):
        from unittest.mock import patch
        import numpy as np
        from scene_rsi.continuous.geometry import rotation
        from scene_rsi.continuous.render import export
        with patch('scene_rsi.continuous.render.export_payload') as renderer:
            export(self.task,self.state,self.directory,'/tmp/unused-continuous-render')
        payload = renderer.call_args.args[0]
        expected = {q['id']:q for q in self.state['objects']}
        for instance in payload['instances']:
            obj = expected[instance['id']]
            a = self.task['spec']['assets'][obj['asset']]
            self.assertEqual(instance['rotation'],obj['rotation'])
            np.testing.assert_allclose(np.array(instance['location'])+rotation(obj['rotation'])@[0,0,a['height']/2],obj['position'])
            self.assertEqual(instance['scale'],[a['width'],a['depth'],a['height']])


if __name__ == '__main__':
    unittest.main()
