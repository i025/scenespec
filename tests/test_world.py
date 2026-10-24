"""Exact game rules, independent certificates, real Bullet execution and HTTP UI."""
from dataclasses import replace
import http.client
from io import BytesIO
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from scene_rsi.evaluator.verifier import SceneVerifier
from scene_rsi.world.task import WorldTask, GeneratorConfig, generate, make_task, transition
from scene_rsi.world.planning import solve, fixed_plan

HAS_BULLET = importlib.util.find_spec('pybullet') is not None


class TestWorldRules(unittest.TestCase):
    def simple(self):
        return make_task(5, 5, {(0, 3)}, (1, 1), (2, 1), (3, 1))

    def test_scene_contract_round_trip_and_original_verifier(self):
        task = self.simple()
        self.assertEqual(task.to_dict(), WorldTask.from_dict(task.to_dict()).to_dict())
        self.assertTrue(SceneVerifier().verify(task.spec, task.initial)['valid'])

    def test_move_and_push_update_only_expected_cells(self):
        task = self.simple()
        state, event = transition(task, task.start, 'up')
        self.assertEqual((state, event), (((1, 2), (2, 1)), 'move'))
        state, event = transition(task, task.start, 'right')
        self.assertEqual((state, event), (((2, 1), (3, 1)), 'push'))

    def test_wall_and_obstacle_prevent_push_without_mutation(self):
        for obstacle in ({(3, 1)}, set()):
            task = make_task(5, 5, obstacle, (1, 1) if obstacle else (2, 1),
                             (2, 1) if obstacle else (3, 1), (1, 3))
            action = 'right'
            if not obstacle:
                state, _ = transition(task, task.start, action)
            else:
                state = task.start
            self.assertEqual(transition(task, state, action), (state, 'collision'))

    def test_corner_is_proven_unsolvable_and_limit_is_unknown(self):
        task = make_task(5, 5, set(), (1, 1), (0, 0), (3, 3))
        self.assertEqual(solve(task)['status'], 'unsolvable')
        self.assertEqual(solve(self.simple(), max_nodes=1)['status'], 'unknown')

    def test_reference_actions_reach_goal_under_exact_transition(self):
        task = generate(2, GeneratorConfig(require_solvable=True))
        state = task.start
        for action in solve(task)['actions']:
            state, event = transition(task, state, action)
            self.assertNotEqual(event, 'collision')
        self.assertEqual(state[1], task.goal)

    def test_agent_failure_does_not_imply_scene_unsolvable(self):
        task = generate(2, GeneratorConfig(require_solvable=True))
        self.assertEqual(solve(task)['status'], 'solvable')
        state = task.start
        for action in fixed_plan(task):
            state, _ = transition(task, state, action)
        self.assertNotEqual(state[1], task.goal)

    def test_generator_is_repeatable_and_certifies_nontrivial_rooms(self):
        config = GeneratorConfig(require_solvable=True)
        for seed in range(10):
            task = generate(seed, config)
            self.assertEqual(task.to_dict(), generate(seed, config).to_dict())
            self.assertEqual(len(task.obstacles), 6)
            self.assertEqual(solve(task)['status'], 'solvable')
            self.assertGreaterEqual(sum(abs(a-b) for a, b in zip(task.start[1], task.goal)), 2)

    def test_invalid_layout_and_non_boolean_generator_options_rejected(self):
        with self.assertRaises(ValueError):
            make_task(5, 5, {(1, 1)}, (1, 1), (2, 1), (3, 1))
        with self.assertRaises(ValueError):
            generate(1, replace(GeneratorConfig(), require_solvable='run-code'))


@unittest.skipUnless(HAS_BULLET, 'install requirements-world.txt to test the optional CPU world')
class TestBulletWorld(unittest.TestCase):
    def test_actual_engine_pushes_to_goal_and_episode_completes(self):
        from scene_rsi.world.engine import World
        task = make_task(5, 5, set(), (1, 1), (2, 1), (3, 1))
        with World(task) as world:
            self.assertTrue(world.collision_free())
            result = world.step('right')
            self.assertEqual(result['event'], 'push')
            self.assertTrue(world.success)
            self.assertTrue(SceneVerifier().verify(task.spec, world.state())['valid'])
            self.assertEqual(world.step('left')['event'], 'complete')
            self.assertEqual(world.steps, 1)

    def test_bullet_detects_real_shape_overlap(self):
        from scene_rsi.world.engine import World
        task = make_task(5, 5, {(2, 2)}, (1, 1), (3, 1), (3, 3))
        with World(task) as world:
            world._pose('agent', (2, 2))
            self.assertFalse(world.collision_free())

    def test_collision_rejects_motion_and_records_event(self):
        from scene_rsi.world.engine import World
        task = make_task(5, 5, {(2, 1)}, (1, 1), (2, 2), (3, 3))
        with World(task) as world:
            initial = world.grid
            result = world.step('right')
            self.assertEqual(world.grid, initial)
            self.assertEqual(result['collisions'], 1)
            self.assertTrue(world.collision_free())

    def test_cpu_render_is_a_real_nonempty_color_image(self):
        from PIL import Image
        from scene_rsi.world.engine import World
        with World(generate(1, GeneratorConfig(require_solvable=True))) as world:
            image = Image.open(BytesIO(world.png(width=320, height=240)))
            self.assertEqual(image.size, (320, 240))
            self.assertGreater(len(image.getcolors(320*240)), 20)

    def test_evaluation_separates_unsolvable_incapable_and_timeout(self):
        from scene_rsi.world.experiments import evaluate
        corner = make_task(5, 5, set(), (1, 1), (0, 0), (3, 3))
        self.assertEqual(evaluate(corner)['outcome'], 'scene_unsolvable')
        detour = generate(2, GeneratorConfig(require_solvable=True))
        result = evaluate(detour)
        self.assertEqual(result['outcome'], 'agent_insufficient')
        self.assertTrue(result['reference_replayed'])
        self.assertEqual(evaluate(detour, max_steps=1)['outcome'], 'agent_timeout')

    def test_actual_reference_replay_matches_search_across_scenes(self):
        from scene_rsi.world.experiments import evaluate
        for seed in range(8):
            result = evaluate(generate(seed, GeneratorConfig(require_solvable=True)))
            self.assertTrue(result['reference_replayed'], (seed, result['outcome']))

    def test_structured_proposals_cannot_change_rules_or_execute_python(self):
        from scene_rsi.world.experiments import experiment
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for changes in ({'python': 'raise SystemExit()'}, {'obstacles': 0}, {'require_solvable': 'yes'}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    experiment(root/'invalid', [{'name': 'bad', 'changes': changes}])
                self.assertFalse((root/'invalid').exists())

    def test_experiment_preserves_geometry_and_keeps_val_out_of_feedback(self):
        from scene_rsi.world.experiments import experiment
        with TemporaryDirectory() as temporary:
            root = Path(temporary)/'run'
            report = experiment(root, [{'name': 'certified', 'changes': {'require_solvable': True}}],
                                dev_seeds=[100, 101], val_seeds=[1000, 1001])
            feedback = json.loads((root/'dev_feedback.json').read_text())
            self.assertNotIn('validation', feedback)
            for trial in feedback['trials']:
                self.assertEqual(set(trial), {'name', 'config', 'dev'})
                self.assertEqual(trial['config']['obstacles'], 6)
            self.assertFalse(report['arbitrary_candidate_code_executed'])
            self.assertTrue((root/'selected_generator.json').is_file())

    def test_live_http_step_reset_render_and_record(self):
        from scene_rsi.world.server import WorldServer
        task = make_task(5, 5, set(), (1, 1), (2, 1), (3, 1))
        config = GeneratorConfig(width=5, depth=5, obstacles=3, require_solvable=True)
        with WorldServer(('127.0.0.1', 0), task, config) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                connection.request('GET', '/')
                self.assertIn(b'Run fixed agent', connection.getresponse().read())
                connection.request('POST', '/step', body=json.dumps({'action':'right'}), headers={'Content-Type':'application/json'})
                self.assertTrue(json.loads(connection.getresponse().read())['success'])
                connection.request('GET', '/record')
                self.assertEqual(len(json.loads(connection.getresponse().read())['trajectory']), 2)
                connection.request('GET', '/frame')
                self.assertTrue(connection.getresponse().read().startswith(b'\x89PNG'))
                connection.request('POST', '/reset', body=json.dumps({'level':'generated','seed':3}), headers={'Content-Type':'application/json'})
                connection.getresponse().read()
                connection.request('GET', '/state')
                generated = json.loads(connection.getresponse().read())
                self.assertEqual(generated['task']['spec']['room']['width'], 5)
                self.assertEqual(generated['initial_solvability'], 'solvable')
                connection.request('POST', '/reset', body=json.dumps({'level':'trap'}), headers={'Content-Type':'application/json'})
                self.assertFalse(json.loads(connection.getresponse().read())['success'])
                connection.request('GET', '/state')
                self.assertEqual(json.loads(connection.getresponse().read())['initial_solvability'], 'unsolvable')
                connection.close()
            finally:
                server.shutdown()
                thread.join()
