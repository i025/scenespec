import copy
import unittest

from scene_rsi.benchmark.spatial_tasks import check, dataset, make_case, navigation, ray_box, visible_fraction
from scene_rsi.schema import SceneSpec


class SpatialTasksTest(unittest.TestCase):
    def test_all_split_witnesses_satisfy_3d_constraints(self):
        for split in ('calibration', 'dev', 'selection', 'test'):
            for case, witness in dataset(split):
                self.assertTrue(check(case, witness)['success'])
                self.assertNotIn('witness', case['packet'])

    def test_elevated_footprints_can_overlap_support(self):
        case, witness = make_case('dev', 'support', 0, 0)
        result = check(case, witness)
        self.assertTrue(result['physical_valid'])
        self.assertTrue(any(z > 0 for z in witness['elevations'].values()))

    def test_floating_and_interpenetration_rejected(self):
        case, witness = make_case('dev', 'support', 1, 0)
        name = next(k for k, r in case['packet']['requirements'].items() if r['support'] != 'floor')
        for dz, expected in ((.1, 'unsupported'), (-.1, '3d_collision')):
            changed = copy.deepcopy(witness)
            changed['elevations'][name] += dz
            self.assertIn(expected, [f['type'] for f in check(case, changed)['failures']])

    def test_room_and_camera_constraints_are_hard(self):
        case, witness = make_case('dev', 'visibility', 1, 0)
        changed = copy.deepcopy(witness)
        hidden = next(k for k, v in case['packet']['visibility'].items() if v[1] == .2)
        target = next(p for p in changed['state']['objects'] if p['id'] == hidden)
        target.update(x=1.5, y=2.5)
        self.assertIn('camera_visibility', [f['type'] for f in check(case, changed)['failures']])
        rule = case['packet']['requirements'][hidden]
        target['x'] = case['packet']['width']-1.5 if rule['room'] == 'left' else 1.5
        self.assertIn('room_assignment', [f['type'] for f in check(case, changed)['failures']])

    def test_fixed_divider_cannot_be_moved(self):
        case, witness = make_case('dev', 'rooms', 0, 0)
        changed = copy.deepcopy(witness)
        fixed = case['packet']['fixed'][0]['id']
        next(p for p in changed['state']['objects'] if p['id'] == fixed)['x'] += .2
        self.assertIn('fixed_object_changed', [f['type'] for f in check(case, changed)['failures']])

    def test_segment_not_infinite_ray(self):
        self.assertTrue(ray_box((0, 0, 0), (4, 0, 0), (1, -1, -1, 2, 1, 1)))
        self.assertFalse(ray_box((0, 0, 0), (4, 0, 0), (5, -1, -1, 6, 1, 1)))
        self.assertFalse(ray_box((0, 0, 0), (4, 0, 0), (1, 1, 1, 2, 2, 2)))

    def test_frustum_and_occlusion(self):
        view = {'position':[0, -3, 1], 'target':[0, 3, 1], 'fov':60}
        boxes = {'target':(-.5, 3, .5, .5, 4, 1.5)}
        self.assertEqual(visible_fraction(view, 'target', boxes), 1)
        boxes['screen'] = (-1, 0, 0, 1, .2, 2)
        self.assertEqual(visible_fraction(view, 'target', boxes), 0)
        view['target'] = [0, -6, 1]
        self.assertEqual(visible_fraction(view, 'target', boxes), 0)

    def test_witness_and_order_not_exposed(self):
        a, _ = make_case('dev', 'support', 2, 0)
        b, _ = make_case('test', 'support', 2, 0)
        self.assertNotEqual(a['packet']['spec']['room'], b['packet']['spec']['room'])
        self.assertNotEqual(a['packet']['domains'], b['packet']['domains'])
        self.assertEqual(set(a['packet']), {'width','depth','obstacles','spec','fixed','domains',
                         'requirements','rooms','view','visibility','distances','start','goal','visual_assets'})

    def test_joint_distance_uses_3d_centres(self):
        case, witness = make_case('dev', 'support', 0, 0)
        relation = case['packet']['distances'][0]
        changed = copy.deepcopy(witness)
        next(p for p in changed['state']['objects'] if p['id'] == relation['a'])['y'] += 3
        self.assertIn('joint_distance', [f['type'] for f in check(case, changed)['failures']])

    def test_passage_requires_horizontal_and_vertical_clearance(self):
        packet = {'width':3, 'depth':3, 'start':[0,0], 'goal':[2,2]}
        self.assertIsNone(navigation(packet, {'beam':(-1, .9, 0, 4, 1.1, 2)}))
        self.assertIsNotNone(navigation(packet, {'beam':(-1, .9, 2, 4, 1.1, 3)}))

    def test_renderer_preserves_elevations_and_uses_checked_camera(self):
        from unittest.mock import patch
        from scene_rsi.render import export_spatial
        case, witness = make_case('dev', 'visibility', 1, 0)
        with patch('scene_rsi.render.export_payload') as render:
            export_spatial(case, witness, '/tmp/unused-render-test')
        payload, verification, _ = render.call_args.args
        self.assertTrue(verification['success'])
        self.assertEqual(payload['view'], case['packet']['view'])
        self.assertTrue(any(p['location'][2] > 0 for p in payload['instances']))
        self.assertEqual({p['id']:p['location'][2] for p in payload['instances']}, witness['elevations'])


if __name__ == '__main__':
    unittest.main()
