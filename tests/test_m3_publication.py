"""Offline publication checks: no model or generated code is executed."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import summarize_m3_validation as publication


class M3PublicationTests(unittest.TestCase):
    def metrics(self, preference=.4):
        return dict(success_rate=1., preference_error_m=preference, visibility_mean=.98,
                    generation_wall_seconds_mean=1., diversity_position_rms_m=.3,
                    valid_pair_diversity_m=.3, diversity_rotation_deg=30.)

    def fixture(self, root):
        run = root / 'run'; run.mkdir(); calls, scores, hidden = [], [], []
        for index, initial in enumerate(('sequential_projection', 'shared_shift')):
            folder = run / initial; folder.mkdir(); cases = {}
            for number, split in enumerate(('dev', 'selection', 'test')):
                seed = 87654321000000 + index * 100 + number * 10
                case = {'task': {'spec': {'room': {'width': 6. + index, 'depth': 6. + number}},
                                 'supports': {}, 'camera': {'name': initial + '_' + split}},
                        'seed': seed, 'algorithm_seeds': [seed + i + 1 for i in range(4)],
                        'reference': {'objects': ['PRIVATE_REFERENCE_' + initial + '_' + split]}}
                cases[split] = [case]
                if split != 'dev': hidden.append(case)
                dest = folder / 'baseline' / split; dest.mkdir(parents=True)
                publication.save(dest / 'summary.json', self.metrics())
                publication.save(dest / '00_0.json', {'private_row': split} if split != 'dev' else {'public_dev': initial})
            publication.save(folder / 'private_tasks.json', cases)
            publication.save(folder / 'evidence_budget.json', {'delivered_examples_each': 8})
            for policy in ('M2', 'M3'):
                for repeat in (0, 1):
                    dest = folder / policy / f'r{repeat}'; dest.mkdir(parents=True)
                    failed = initial == 'shared_shift' and (policy == 'M3' or repeat == 1)
                    selected = (initial, policy, repeat) in (
                        ('sequential_projection', 'M3', 0), ('shared_shift', 'M2', 0))
                    record = dict(initial=initial, policy=policy, repeat=repeat, K=1,
                                  status='candidate_failed' if failed else 'evaluated', selected=selected,
                                  evaluation_wall_seconds={} if failed else {'dev': 2., 'selection': 3., 'test': 5.})
                    calls.append(record); publication.save(dest / 'record.json', record)
                    publication.save(dest / 'task.json', {'feedback': {'examples': [cases['dev'][0]['task']]}})
                    coding = dest / 'coding'; coding.mkdir()
                    usage = None if failed else {'input_tokens': 8, 'output_tokens': 2, 'total_tokens': 10}
                    publication.save(coding / 'backend_result.json', {
                        'status': 'failed' if failed else 'completed', 'usage': usage, 'wall_seconds': 1. if failed else 7.})
                    publication.save(coding / 'model_calls.json', {
                        'requests': [{'usage': usage, 'http_status': 429 if failed else 200}]})
                    (coding / 'events.jsonl').write_text(json.dumps({'type': 'turn.failed' if failed else 'turn.completed'}) + '\n')
                    (coding / 'response_0.sse').write_text('data: ' + json.dumps({'status': 429 if failed else 200}) + '\n\n')
                    (coding / 'candidate.diff').write_text('' if failed else 'trusted fixture patch\n')
                    (coding / 'candidate.py').write_text('' if failed else '# Generated fixture; never executed.\n')
                    raw = None
                    if not failed:
                        raw = self.metrics(.3)
                        (dest / 'source.py').write_text('# Archive fixture only; never executed.\n')
                        for split in ('dev', 'selection', 'test'):
                            target = dest / split; target.mkdir()
                            publication.save(target / 'summary.json', raw)
                            publication.save(target / '00_0.json', {'public_dev': initial} if split == 'dev' else {'private_row': split})
                    scores.append(dict(initial=initial, policy=policy, repeat=repeat, selected=selected,
                                       before=self.metrics(), raw_candidate=raw,
                                       selected_after=raw if selected else self.metrics(),
                                       heldout_multiobjective_guard=selected,
                                       selected_preference_improvement_m=.1 if selected else 0.))
        for name, value in (('calls', calls), ('scores', scores), ('finished', {'formal_coding_tasks': 8})):
            publication.save(run / (name + '.json'), value)
        return run, hidden

    def publish(self, run, out):
        with patch.object(publication.sys, 'argv', ['publish', '--run', str(run), '--out', str(out)]), \
             redirect_stdout(io.StringIO()):
            publication.main()

    def test_all_eight_attempts_and_artifacts_publish_with_conditional_rates(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); run, hidden = self.fixture(root); out = root / 'public'
            self.publish(run, out)
            rows = publication.read(out / 'trajectories.json')
            self.assertEqual(len(rows), 8)
            self.assertEqual(sum(r['coding_cost']['infrastructure_failure'] for r in rows), 3)
            groups = publication.read(out / 'costs.json')['formal_groups']
            summary = publication.read(out / 'summary.json')['policies']
            for policy, completed, unknown in (('M2', 3, 1), ('M3', 2, 2)):
                group, report = groups[policy], summary[policy]
                self.assertIsNone(group['usage'])
                self.assertEqual(group['unknown_usage_requests'], unknown)
                self.assertEqual(group['known_usage_lower_bound']['total_tokens'], completed * 10)
                self.assertIsNone(report['tokens_per_effective_improvement'])
                self.assertEqual(report['tokens_per_effective_improvement_known_lower_bound'], completed * 10)
                self.assertEqual(report['conditional_on_backend_completion']['trajectories'], completed)
                self.assertEqual(report['conditional_on_candidate_evaluation']['selection_acceptance_rate'], 1 / completed)
                self.assertEqual(report['conditional_on_candidate_evaluation']['selected_complete_effective_improvement_rate'], 1 / completed)
                self.assertEqual(report['selected_complete_effective_improvement_rate'], .25)
            for row in rows:
                relative = Path(row['initial']) / row['policy'] / f'r{row["repeat"]}'
                for name in ('events.jsonl', 'response_0.sse', 'candidate.diff', 'candidate.py',
                             'model_calls.json', 'backend_result.json'):
                    self.assertEqual((out / relative / 'coding' / name).read_bytes(),
                                     (run / relative / 'coding' / name).read_bytes())
                self.assertTrue((out / relative / 'record.json').exists())
                for split in ('selection', 'test'):
                    self.assertFalse((out / relative / split).exists())
            for initial in ('sequential_projection', 'shared_shift'):
                self.assertTrue((out / initial / 'evidence_budget.json').exists())
                self.assertFalse((out / initial / 'private_tasks.json').exists())
            texts = '\n'.join(p.read_text() for p in out.rglob('*') if p.is_file())
            self.assertNotIn('PRIVATE_REFERENCE_', texts)
            self.assertNotIn('"private_row"', texts)
            for case in hidden:
                for seed in (case['seed'], *case['algorithm_seeds']):
                    self.assertNotIn(str(seed), texts)
            self.assertEqual(publication.read(out / 'privacy.json')['hidden_packet_matches'], 0)

    def test_private_packet_or_stream_seed_leak_is_rejected(self):
        for leak in ('packet', 'stream_seed'):
            with self.subTest(leak=leak), TemporaryDirectory() as directory:
                root = Path(directory); run, hidden = self.fixture(root)
                dest = run / 'sequential_projection/M2/r0'
                if leak == 'packet':
                    publication.save(dest / 'task.json', {'feedback': hidden[0]['task']})
                else:
                    (dest / 'coding/response_0.sse').write_text('data: ' + str(hidden[0]['seed']))
                with self.assertRaises(AssertionError):
                    self.publish(run, root / 'public')

    def test_missing_formal_trajectory_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); run, _ = self.fixture(root)
            publication.save(run / 'calls.json', publication.read(run / 'calls.json')[:-1])
            with self.assertRaises(AssertionError):
                self.publish(run, root / 'public')

    def test_unreported_usage_and_evaluation_remain_unknown(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            usage = {'input_tokens': 11, 'output_tokens': 3, 'total_tokens': 14}
            publication.save(folder / 'backend_result.json', {'status': 'failed', 'usage': None})
            publication.save(folder / 'model_calls.json', {'requests': [
                {'http_status': 200, 'usage': usage}, {'http_status': 429, 'usage': None}]})
            row = publication.cost(folder, {}, 'formal'); group = publication.cost_group([row])
            self.assertIsNone(row['usage']); self.assertIsNone(group['usage'])
            self.assertEqual(row['known_usage_lower_bound'], usage)
            self.assertEqual(group['known_usage_lower_bound'], usage)
            self.assertEqual(group['unknown_usage_requests'], 1)
            self.assertEqual(row['failure_classification'], 'infrastructure_http_429')

        with TemporaryDirectory() as directory:
            folder = Path(directory)
            unknown = publication.cost(folder, {'status': 'evaluated'}, 'development_ablation')
            unexecuted = publication.cost(folder, {'status': 'candidate_failed'}, 'formal')
            measured = publication.cost(folder, {'status': 'evaluated', 'evaluation_wall_seconds': {
                'dev': 2., 'selection': 3., 'test': 5.}}, 'formal')
            self.assertIsNone(unknown['evaluation_wall_seconds_sum'])
            self.assertEqual(unexecuted['evaluation_wall_seconds_sum'], 0)
            self.assertEqual(measured['evaluation_wall_seconds_sum'], 10.)
            self.assertIsNone(publication.cost_group([unknown, measured])['evaluation_wall_seconds_sum'])
            self.assertEqual(publication.cost_group([unexecuted, measured])['evaluation_wall_seconds_sum'], 10.)


if __name__ == '__main__':
    unittest.main()
