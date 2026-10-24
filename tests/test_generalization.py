"""New protocol checks; original verifier, solver, tasks and tests stay unchanged."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from scene_rsi.world.generalization import cases, map_feasibility, evaluate_cases, route_metrics
from scene_rsi.world.planning import solve
from scene_rsi.world.task import make_task


class GeneralizationTests(unittest.TestCase):
    def packet(self, free):
        obstacles = [(x, y) for x in range(4) for y in range(4) if (x, y) not in free]
        return {'width':4, 'depth':4, 'obstacles':len(obstacles),
                'sample':obstacles+list(free[:3])}

    def test_exact_impossible_and_positive_witness(self):
        impossible = self.packet([(0, 0), (3, 0), (0, 3), (3, 3)])
        self.assertEqual(map_feasibility(impossible)['status'], 'impossible')
        corridor = self.packet([(0, 0), (1, 0), (2, 0), (3, 0)])
        result = map_feasibility(corridor)
        self.assertEqual(result['status'], 'feasible')
        from scene_rsi.world.task import WorldTask
        self.assertEqual(solve(WorldTask.from_dict(result['witness']))['status'], 'solvable')

    def test_exact_classification_agrees_with_all_small_starts(self):
        free = [(0, 0), (1, 0), (1, 1), (2, 1)]
        packet = self.packet(free)
        possible = False
        for a in free:
            for c in free:
                for g in free:
                    if len({a, c, g}) == 3 and abs(c[0]-g[0])+abs(c[1]-g[1]) >= 2:
                        possible |= solve(make_task(4, 4, packet['sample'][:-3], a, c, g))['status'] == 'solvable'
        self.assertEqual(map_feasibility(packet)['status'] == 'feasible', possible)

    def test_registered_conditions_are_deterministic_and_legal(self):
        data = cases()
        self.assertEqual(data, cases())
        self.assertEqual(len(data), 192)
        self.assertEqual(len({r['id'] for r in data}), 192)
        for row in data:
            p = row['packet']
            sample = [tuple(c) for c in p['sample']]
            self.assertEqual(len(sample), p['obstacles']+3)
            self.assertEqual(len(set(sample)), len(sample))
            self.assertGreaterEqual(abs(sample[-2][0]-sample[-1][0])+abs(sample[-2][1]-sample[-1][1]), 2)

    def test_distance_failure_has_no_second_attempt(self):
        case = cases(repeats=1)[0]
        p = case['packet']
        free = [(x, y) for x in range(p['width']) for y in range(p['depth']) if (x, y) not in p['sample'][:-3]]
        adjacent = next((c, g) for c in free for g in free if abs(c[0]-g[0])+abs(c[1]-g[1]) == 1)
        a = next(c for c in free if c not in adjacent)
        response = {'obstacles':p['sample'][:-3], 'agent':a, 'crate':adjacent[0], 'goal':adjacent[1]}
        with tempfile.TemporaryDirectory() as temp, patch('scene_rsi.world.generalization.LayoutProcess') as cls:
            cls.return_value.request.return_value = response
            cls.return_value.close.return_value = {}
            report, rows = evaluate_cases('def layout(p): return p', [case], temp, Path(temp)/'result')
            cls.return_value.request.assert_called_once()
            self.assertEqual(report['all']['valid'], 0)
            self.assertEqual(rows[0]['failure_type'], 'generation_error')

    def test_turns_distinguish_walk_and_push_directions(self):
        task = make_task(4, 4, [], (0, 1), (1, 1), (3, 2))
        self.assertEqual(route_metrics(task, ['right', 'right', 'down']),
                         {'action_turns':1, 'push_turns':0})


if __name__ == '__main__':
    unittest.main()
