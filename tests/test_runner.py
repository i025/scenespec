"""Accounting and final-test control checks; mocked failures are trusted fixtures."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scene_rsi.agent.fixed_agent import FixedSceneAgent
from scene_rsi.benchmark import runner
from scene_rsi.evaluator.verifier import SceneVerifier
from scene_rsi.schema import SceneSpec, SceneState


def make_task(task_id="simple", family="simple"):
    spec = SceneSpec.from_dict({
        "scene_id": task_id,
        "room": {"width": 4, "depth": 4, "height": 3},
        "assets": {"box": {"width": .5, "depth": .5, "height": 1}},
        "required_objects": [{"id": "box", "asset": "box"}],
    })
    digest = hashlib.sha256(json.dumps(asdict(spec), sort_keys=True).encode()).hexdigest()
    return SimpleNamespace(spec=spec, task_id=task_id, family=family, spec_sha256=digest)


class TestBenchmarkAccounting(unittest.TestCase):
    def test_failure_is_in_denominator_and_diagnostics_are_separate(self):
        tasks = [make_task("ok"), make_task("crash")]
        good = FixedSceneAgent().generate(tasks[0].spec)
        with patch.object(runner.FixedSceneAgent, "generate", side_effect=[good, RuntimeError("fixture crash")]):
            report = runner.evaluate_tasks(tasks)
        self.assertEqual((report["n"], report["n_valid"], report["n_failed"], report["n_errors"]), (2, 1, 1, 1))
        self.assertEqual(report["valid_rate"], .5)
        self.assertEqual(report["diagnostic_score_n"], 1)
        self.assertEqual(report["failure_type_counts"], {"agent_exception": 1})
        row = report["results"][1]
        self.assertFalse(row["valid"])
        self.assertEqual((row["status"], row["stage"]), ("agent_exception", "generate"))
        self.assertIsNone(row["scores"])
        self.assertEqual(row["error"]["type"], "RuntimeError")

    def test_micro_and_macro_family_rates_differ_for_unequal_sizes(self):
        tasks = [make_task("easy_1", "easy"), make_task("easy_2", "easy"), make_task("hard", "hard")]
        states = [FixedSceneAgent().generate(task.spec) for task in tasks[:2]]
        states.append(SceneState("hard", ()))
        with patch.object(runner.FixedSceneAgent, "generate", side_effect=states):
            report = runner.evaluate_tasks(tasks)
        self.assertAlmostEqual(report["micro_success_rate"], 2 / 3)
        self.assertEqual(report["macro_family_success_rate"], .5)
        self.assertEqual(report["by_family"]["hard"]["n_failed"], 1)
        self.assertEqual(report["by_complexity"]["objects=1|constraints=0|paths=0"]["n"], 3)
        ci = report["success_rate_wilson95"]
        self.assertLess(ci["lower"], 2 / 3)
        self.assertGreater(ci["upper"], 2 / 3)
        self.assertIn("IID approximation", ci["assumption"])

    def test_original_verifier_payload_and_constraint_attribution_preserved(self):
        root = Path(__file__).resolve().parents[1]
        path = root / "benchmarks/dev/corner_desk_challenge.json"
        spec = SceneSpec.load(path)
        task = SimpleNamespace(spec=spec, family="corner", task_id=spec.scene_id,
                               spec_sha256=runner.sha256_file(path))
        expected = SceneVerifier().verify(spec, FixedSceneAgent().generate(spec))
        report = runner.evaluate_tasks([task])
        row = report["results"][0]
        self.assertEqual(row["failures"], expected["failures"])
        self.assertEqual(row["scores"], expected["scores"])
        self.assertEqual(row["counts"], expected["counts"])
        self.assertEqual(row["status"], "verification_failure")
        self.assertTrue(row["constraint_failures"])
        self.assertEqual(set(report["constraint_type_failure_counts"]), {"near_wall"})
        self.assertEqual(report["n_errors"], 0)

    def test_bad_state_and_timeout_are_reported_without_dropping_tasks(self):
        tasks = [make_task("invalid"), make_task("timeout")]
        with patch.object(runner.FixedSceneAgent, "generate", side_effect=[{}, TimeoutError("fixture timeout")]):
            report = runner.evaluate_tasks(tasks)
        self.assertEqual(report["n"], 2)
        self.assertEqual(report["n_valid"], 0)
        self.assertEqual(report["status_counts"], {"agent_timeout": 1, "invalid_state": 1})
        self.assertIsNone(report["average_diagnostic_score"])
        self.assertFalse(report["provenance"]["execution"]["per_task_timeout_enforced"])

    def test_agent_cannot_mutate_catalog_used_by_evaluator(self):
        task = make_task()
        original = deepcopy(task.spec.assets)
        def fixture(spec):
            spec.assets.clear()
            return SceneState(spec.scene_id, ())
        with patch.object(runner.FixedSceneAgent, "generate", side_effect=fixture):
            report = runner.evaluate_tasks([task])
        self.assertEqual(task.spec.assets, original)
        self.assertEqual(report["results"][0]["status"], "verification_failure")

    def test_empty_and_duplicate_tasks_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            runner.evaluate_tasks([])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            runner.evaluate_tasks([make_task(), make_task()])

    def test_source_fingerprints_and_resource_scope_recorded(self):
        task = make_task()
        report = runner.evaluate_tasks([task], split="dev")
        self.assertEqual(report["split"], "dev")
        self.assertEqual(report["results"][0]["spec_sha256"], task.spec_sha256)
        hashes = report["provenance"]["source_sha256"]
        evaluator = "scene_rsi/evaluator/verifier.py"
        self.assertEqual(hashes[evaluator], runner.sha256_file(runner.SOURCE_ROOT / evaluator))
        self.assertTrue(report["source_unchanged_during_run"])
        self.assertIn("commit", report["provenance"]["git"])
        resources = report["results"][0]["resources"]
        self.assertGreaterEqual(resources["wall_seconds"], 0)
        self.assertGreaterEqual(resources["cpu_seconds"], 0)
        self.assertGreater(resources["process_peak_rss_bytes"], 0)
        self.assertIn("process lifetime", resources["rss_scope"])
        self.assertEqual(resources["gpu_seconds"], 0)

    def test_legacy_runner_writes_structured_report(self):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            out = Path(directory) / "report.json"
            report = runner.run_specs([root / "examples/bedroom_001.json"], out)
            self.assertEqual(json.loads(out.read_text()), report)
            self.assertEqual((report["n"], report["n_valid"]), (1, 1))


class TestDatasetRunControls(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "dataset"
        self.root.mkdir()
        self.output_root = Path(self.tmp.name) / "reports"
        (self.root / "manifest.json").write_text('{"fixture":true}\n')
        self.task = make_task()
        self.commit = "a" * 40
        self.git = {"available": True, "dirty": False, "commit": self.commit, "status": []}

    def test_dev_report_records_manifest_and_selected_split_only(self):
        with patch("scene_rsi.benchmark.dataset.load_dataset", return_value=[self.task]) as load:
            result = runner.run_dataset(self.root, "dev", self.output_root / "result.json")
        load.assert_called_once_with(self.root, "dev")
        self.assertEqual(result["provenance"]["dataset_manifest_sha256"], runner.sha256_file(self.root / "manifest.json"))
        self.assertFalse((self.root / "private/final_test_ledger.json").exists())

    def test_reports_cannot_overwrite_dataset(self):
        before = (self.root / "manifest.json").read_bytes()
        with patch("scene_rsi.benchmark.dataset.load_dataset") as load:
            with self.assertRaisesRegex(ValueError, "outside the dataset"):
                runner.run_dataset(self.root, "dev", self.root / "manifest.json")
            load.assert_not_called()
        self.assertEqual((self.root / "manifest.json").read_bytes(), before)

    def test_legacy_runner_rejects_managed_tasks_before_reading(self):
        task_path = self.root / "test/heldout.json"
        task_path.parent.mkdir()
        task_path.write_text("private task must not be opened")
        with patch.object(runner.SceneSpec, "load") as load:
            with self.assertRaisesRegex(ValueError, "use evaluate"):
                runner.run_specs([task_path], self.output_root / "report.json")
            load.assert_not_called()
        alias = Path(self.tmp.name) / "alias.json"
        alias.symlink_to(task_path)
        with self.assertRaisesRegex(ValueError, "use evaluate"):
            runner.reject_managed_dataset_input(alias)

    def test_private_directory_cannot_escape_via_symlink(self):
        escaped = Path(self.tmp.name) / "outside"
        escaped.mkdir()
        (self.root / "private").symlink_to(escaped, target_is_directory=True)
        with patch.object(runner, "git_provenance", return_value=self.git):
            with self.assertRaisesRegex(ValueError, "symlinks"):
                runner.run_dataset(self.root, "test", self.root / "private/test_results/report.json",
                                   final_test=True, frozen_commit=self.commit)

    def test_test_requires_explicit_final_option_before_dataset_read(self):
        with patch("scene_rsi.benchmark.dataset.load_dataset") as load:
            with self.assertRaisesRegex(ValueError, "explicit"):
                runner.run_dataset(self.root, "test", self.root / "report.json")
            load.assert_not_called()

    def test_test_requires_clean_frozen_commit_and_private_result(self):
        out = self.root / "private/test_results/result.json"
        with patch("scene_rsi.benchmark.dataset.load_dataset") as load:
            with self.assertRaisesRegex(ValueError, "frozen"):
                runner.run_dataset(self.root, "test", out, final_test=True)
            with patch.object(runner, "git_provenance", return_value={**self.git, "dirty": True}):
                with self.assertRaisesRegex(ValueError, "clean"):
                    runner.run_dataset(self.root, "test", out, final_test=True, frozen_commit=self.commit)
            with patch.object(runner, "git_provenance", return_value=self.git):
                with self.assertRaisesRegex(ValueError, "private/test_results"):
                    runner.run_dataset(self.root, "test", self.root / "public.json", final_test=True, frozen_commit=self.commit)
            load.assert_not_called()

    def test_final_test_is_reserved_before_read_and_cannot_repeat(self):
        out = self.root / "private/test_results/result.json"
        ledger = self.root / "private/final_test_ledger.json"
        def load_fixture(root, split):
            self.assertEqual(split, "test")
            self.assertEqual(json.loads(ledger.read_text())["status"], "reserved")
            return [self.task]
        with patch.object(runner, "git_provenance", return_value=self.git):
            with patch("scene_rsi.benchmark.dataset.load_dataset", side_effect=load_fixture) as load:
                result = runner.run_dataset(self.root, "test", out, final_test=True, frozen_commit=self.commit)
                self.assertEqual(result["split"], "test")
                self.assertEqual(load.call_count, 1)
                saved = json.loads(ledger.read_text())
                self.assertEqual(saved["status"], "completed")
                self.assertEqual(saved["report_sha256"], runner.sha256_file(out))
                with self.assertRaisesRegex(ValueError, "already"):
                    runner.run_dataset(self.root, "test", out.with_name("retry.json"), final_test=True, frozen_commit=self.commit)
                self.assertEqual(load.call_count, 1)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        self.assertEqual(ledger.stat().st_mode & 0o777, 0o600)

    def test_interrupted_or_failed_final_test_consumes_reservation(self):
        out = self.root / "private/test_results/failed.json"
        ledger = self.root / "private/final_test_ledger.json"
        with patch.object(runner, "git_provenance", return_value=self.git):
            with patch("scene_rsi.benchmark.dataset.load_dataset", side_effect=RuntimeError("fixture load failure")):
                with self.assertRaisesRegex(RuntimeError, "fixture load"):
                    runner.run_dataset(self.root, "test", out, final_test=True, frozen_commit=self.commit)
            self.assertEqual(json.loads(ledger.read_text())["status"], "reserved")
            with self.assertRaisesRegex(ValueError, "already"):
                runner.run_dataset(self.root, "test", out, final_test=True, frozen_commit=self.commit)
        self.assertFalse(out.exists())

    def test_commit_changed_after_reservation_is_rejected_before_task_read(self):
        out = self.root / "private/test_results/changed.json"
        changed = {**self.git, "commit": "b" * 40}
        with patch.object(runner, "git_provenance", side_effect=[self.git, changed]):
            with patch("scene_rsi.benchmark.dataset.load_dataset") as load:
                with self.assertRaisesRegex(ValueError, "matching frozen_commit"):
                    runner.run_dataset(self.root, "test", out, final_test=True, frozen_commit=self.commit)
                load.assert_not_called()
        ledger = self.root / "private/final_test_ledger.json"
        self.assertEqual(json.loads(ledger.read_text())["status"], "reserved")
        self.assertFalse(out.exists())

    def test_commit_changed_before_execution_is_rejected_even_if_stable_afterwards(self):
        out = self.root / "private/test_results/changed.json"
        changed = {**self.git, "commit": "b" * 40}
        # Reservation and before-read checks see A; evaluation starts at B.
        # B staying unchanged cannot satisfy a reservation for A.
        with patch.object(runner, "git_provenance", side_effect=[self.git, self.git, changed, changed]):
            with patch("scene_rsi.benchmark.dataset.load_dataset", return_value=[self.task]):
                with patch.object(runner.FixedSceneAgent, "generate") as generate:
                    with self.assertRaisesRegex(ValueError, "matching frozen_commit"):
                        runner.run_dataset(self.root, "test", out, final_test=True, frozen_commit=self.commit)
                    generate.assert_not_called()
        ledger = self.root / "private/final_test_ledger.json"
        self.assertEqual(json.loads(ledger.read_text())["status"], "reserved")
        self.assertFalse(out.exists())

    def test_commit_changed_during_execution_keeps_reservation_consumed(self):
        out = self.root / "private/test_results/changed.json"
        changed = {**self.git, "commit": "b" * 40}
        with patch.object(runner, "git_provenance", side_effect=[self.git, self.git, self.git, changed]):
            with patch("scene_rsi.benchmark.dataset.load_dataset", return_value=[self.task]):
                with self.assertRaisesRegex(ValueError, "matching frozen_commit"):
                    runner.run_dataset(self.root, "test", out, final_test=True, frozen_commit=self.commit)
        ledger = self.root / "private/final_test_ledger.json"
        self.assertEqual(json.loads(ledger.read_text())["status"], "reserved")
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
