"""Scope/contract tests. No model-generated module is imported by these tests."""
import unittest
from scene_rsi.world.algorithm_experiment import audit,build_task
from scene_rsi.world.task import GeneratorConfig


class TestAlgorithmProtocol(unittest.TestCase):
    def setUp(self):
        self.config=GeneratorConfig(5,5,1)
        self.sample=[[0,4],[1,1],[2,1],[4,1]]
        self.response={'obstacles':[[0,4]],'agent':[1,1],'crate':[2,1],'goal':[4,1]}

    def test_unchanged_spec_and_state_are_built_outside_candidate(self):
        task=build_task(self.response,self.sample,self.config,42,0)
        self.assertEqual(task.start,((1,1),(2,1)))
        self.assertEqual(task.goal,(4,1))
        self.assertEqual(task.spec.room.height,3)
        self.assertEqual(task.spec.assets['crate'].width,.74)

    def test_static_obstacle_map_cannot_be_simplified(self):
        for obstacles in ([],[[1,4]],[[0,4],[0,3]]):
            with self.assertRaises(ValueError):
                build_task({**self.response,'obstacles':obstacles},self.sample,self.config,42,0)

    def test_geometry_and_two_cell_goal_distance_remain_hard_requirements(self):
        for change in ({'agent':[0,4]},{'agent':[5,1]},{'crate':[1,1]},{'goal':[True,1]}):
            with self.assertRaises(ValueError):
                build_task({**self.response,**change},self.sample,self.config,42,0)
        self.assertIsNone(build_task({**self.response,'goal':[3,1]},self.sample,self.config,42,0))

    def test_candidate_cannot_submit_scores_or_override_budget(self):
        for extra in ({'valid':True},{'attempts':1},{'require_solvable':False}):
            with self.assertRaises(ValueError):
                build_task({**self.response,**extra},self.sample,self.config,42,0)

    def test_audit_rejects_protected_imports_and_top_level_execution(self):
        for source in ('import os\ndef layout(p): return p\n',
                       'from scene_rsi.world.planning import solve\ndef layout(p): return p\n',
                       'print(1)\ndef layout(p): return p\n',
                       'def layout(p): return p.__class__\n'):
            with self.assertRaises(ValueError):
                audit(source)

    def test_pure_algorithm_source_is_audited_as_data_only(self):
        report=audit('from collections import deque\ndef layout(p): return dict(p)\n')
        self.assertTrue(report['passed'])
        self.assertIn('not the execution boundary',report['scope'])
