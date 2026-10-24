"""Controller protocol tests; malicious source remains inert, never executed."""
from dataclasses import asdict, replace
import difflib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scene_rsi.benchmark.dataset import build_dataset
from scene_rsi.meta.fixed import FixedMetaConfig, prepare_request, record_candidate, _git
from scene_rsi.meta.candidate_runner import (
    CandidateRunner, RunLimits, load_audited_candidate, offline_capture, parse_spec, parse_state,
)
from scene_rsi.meta.candidate_evaluation import evaluate_execution


ROOT = Path(__file__).resolve().parents[1]
AGENT = "scene_rsi/agent/fixed_agent.py"
SPEC = {"scene_id": "transport-fixture", "room": {"width": 4, "depth": 4, "height": 3},
        "assets": {"box": {"width": 1, "depth": 1, "height": 1}},
        "required_objects": [{"id": "box_1", "asset": "box"}]}
STATE = {"scene_id": "transport-fixture", "objects": [
    {"id": "box_1", "asset": "box", "x": 2, "y": 2, "yaw_deg": 0}]}


class TestCandidateRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.repo = cls.root / "repo"
        cls.repo.mkdir()
        for name in (AGENT, "scene_rsi/agent/__init__.py", "scene_rsi/schema.py", "scene_rsi/geometry.py"):
            path = cls.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)
        _git(cls.repo, "init", "--quiet")
        _git(cls.repo, "add", ".")
        _git(cls.repo, "-c", "user.name=Candidate Test", "-c", "user.email=test@invalid",
             "commit", "-qm", "trusted fixture")
        cls.dataset, cls.bundle, cls.audit = (cls.root / name for name in ("dataset", "bundle", "audit"))
        build_dataset(cls.dataset, seed=671, per_family=1)
        prepare_request(cls.repo, cls.dataset, cls.bundle, config=FixedMetaConfig(max_candidates=16))
        cls.good_record = cls.record("\n# Static runner contract fixture.\n")
        cls.candidate = load_audited_candidate(Path(cls.good_record["record_path"]).parent)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def record(cls, suffix, path=AGENT):
        before = (cls.repo / path).read_text() if (cls.repo / path).exists() else ""
        text = f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(
            before.splitlines(keepends=True), (before + suffix).splitlines(keepends=True),
            fromfile="a/" + path, tofile="b/" + path))
        proposal = cls.root / "proposal.patch"
        proposal.write_text(text)
        return record_candidate(cls.repo, cls.bundle, proposal, cls.audit)

    def test_audited_git_version_still_blocks_and_has_no_candidate_metrics(self):
        result = CandidateRunner().run(self.candidate, json.dumps(SPEC).encode())
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["candidate"]["commit"], self.good_record["candidate_git"]["commit"])
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["limits_enforced"])
        self.assertIsNone(result["exit_code"])
        self.assertIsNone(result["wall_seconds"])
        self.assertTrue(all(v is None for v in result["resources"].values()))
        self.assertIsNone(evaluate_execution(json.dumps(SPEC).encode(), result)["verdict"])

    def test_caller_flags_cannot_enable_backend(self):
        runner = CandidateRunner()
        runner.verified = True
        runner.backend = "/bin/python"
        self.assertEqual(runner.run(self.candidate, json.dumps(SPEC).encode())["status"], "blocked")
        with self.assertRaises(TypeError):
            CandidateRunner(verified=True)

    def test_forged_version_does_not_match_controller_audit(self):
        with self.assertRaisesRegex(ValueError, "metadata differs"):
            CandidateRunner().run(replace(self.candidate, commit="0" * 40), json.dumps(SPEC).encode())

    def test_rejected_verifier_patch_cannot_be_loaded(self):
        record = self.record("# rejected\n", path="scene_rsi/evaluator/verifier.py")
        self.assertEqual(record["status"], "rejected")
        with self.assertRaisesRegex(ValueError, "accepted controller"):
            load_audited_candidate(Path(record["record_path"]).parent)

    def test_symlink_to_audit_is_rejected(self):
        alias = self.root / "audit-alias"
        alias.symlink_to(self.audit, target_is_directory=True)
        with self.assertRaises(OSError):
            load_audited_candidate(alias / self.candidate.candidate_id)

    def test_malicious_sources_are_only_audited_and_never_started(self):
        marker = self.root / "EXECUTED"
        bodies = {
            "private_read": "open('/private/canary').read()",
            "network": "import socket\nsocket.create_connection(('example.com',443))",
            "infinite_loop": "while True:\n    pass",
            "process_abuse": "import os\nos.fork()",
            "excess_output": "print('x' * 1000000000)",
            "verifier_write": "open('/trusted/scene_rsi/evaluator/verifier.py','w').write('bad')",
        }
        original = subprocess.Popen
        def only_git(command, *args, **kwargs):
            self.assertEqual(command[0], "git", command)
            return original(command, *args, **kwargs)
        with patch.object(subprocess, "Popen", side_effect=only_git):
            for name, body in bodies.items():
                with self.subTest(name=name):
                    record = self.record(f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n{body}\n")
                    self.assertEqual(record["status"], "accepted_static_only")
                    candidate = load_audited_candidate(Path(record["record_path"]).parent)
                    result = CandidateRunner().run(candidate, json.dumps(SPEC).encode())
                    self.assertEqual(result["status"], "blocked")
                    self.assertFalse(marker.exists())

    def test_invalid_limits_and_non_spec_inputs_are_rejected(self):
        for key in asdict(RunLimits()):
            for value in (0, -1, True, 1.5):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    CandidateRunner(replace(RunLimits(), **{key: value}))
        with self.assertRaises(ValueError):
            parse_spec(json.dumps({**SPEC, "controller_log": "PRIVATE"}).encode(), RunLimits())
        with self.assertRaises(ValueError):
            parse_spec(b"x" * 33, RunLimits(input_bytes=32))


class TestOfflineTransportAndIndependentEvaluation(unittest.TestCase):
    def capture(self, state=STATE, **kwargs):
        return offline_capture(json.dumps(state).encode(), **kwargs)

    def score(self, capture):
        return evaluate_execution(json.dumps(SPEC).encode(), capture)

    def test_valid_fixture_uses_trusted_verifier_without_live_evidence(self):
        result = self.score(self.capture())
        self.assertTrue(result["verdict"]["valid"])
        self.assertEqual(result["mode"], "offline_fixture")
        self.assertFalse(result["execution"]["execution_started"])
        self.assertIsNone(result["dynamic_execution_passed"])
        self.assertIsNone(result["scene_generation_improved"])
        self.assertFalse(result["is_rsi"])

    def test_verifier_keeps_hard_failures_when_diagnostic_scores_are_unchanged(self):
        state = {**STATE, "scene_id": "wrong-scene"}
        verdict = self.score(self.capture(state))["verdict"]
        self.assertFalse(verdict["valid"])
        self.assertIn("scene_id_mismatch", {v["type"] for v in verdict["failures"]})
        self.assertEqual(verdict["scores"], self.score(self.capture())["verdict"]["scores"])

    def test_yaw_and_missing_objects_remain_verifier_decisions(self):
        for state, kind in (({**STATE, "objects": []}, "missing_object"),
                            ({**STATE, "objects": [{**STATE["objects"][0], "yaw_deg": 45}]}, "invalid_transform")):
            with self.subTest(kind=kind):
                capture = self.capture(state)
                self.assertEqual(capture["status"], "state_received")
                self.assertIn(kind, {v["type"] for v in self.score(capture)["verdict"]["failures"]})

    def test_untrusted_valid_and_scores_fields_are_rejected(self):
        for key in ("valid", "scores", "resources"):
            self.assertEqual(self.capture({**STATE, key: True})["status"], "invalid_state")

    def test_duplicate_keys_nonfinite_values_and_non_json_are_rejected(self):
        for raw in (b'{"scene_id":"a","scene_id":"b","objects":[]}', b'NaN', b'1e10000',
                    b'\xff', b'__import__("os").system("false")', b'\x80\x04pickle'):
            with self.subTest(raw=raw):
                self.assertEqual(offline_capture(raw)["status"], "invalid_state")
        with self.assertRaises(ValueError):
            parse_state(json.dumps({**STATE, "objects": [{**STATE["objects"][0], "x": True}]}).encode(), RunLimits())

    def test_stdout_and_stderr_overflow_are_bounded_and_not_scored(self):
        for stdout, stderr in ((b"x" * 65, b""), (b"{}", b"x" * 33)):
            result = offline_capture(stdout, stderr=stderr, limits=RunLimits(stdout_bytes=64, stderr_bytes=32))
            self.assertEqual(result["status"], "output_limit")
            self.assertLessEqual(result["logs"]["stdout_bytes"], 64)
            self.assertLessEqual(result["logs"]["stderr_bytes"], 32)
            with patch("scene_rsi.meta.candidate_evaluation.SceneVerifier") as verifier:
                report = self.score(result)
            verifier.assert_not_called()
            self.assertFalse(report["verdict"]["valid"])

    def test_simulated_runtime_failures_skip_verifier(self):
        for termination in ("timeout", "cpu_limit", "memory_limit", "pid_limit", "runtime_error"):
            with self.subTest(termination=termination):
                capture = self.capture(termination=termination)
                with patch("scene_rsi.meta.candidate_evaluation.SceneVerifier") as verifier:
                    verdict = self.score(capture)["verdict"]
                verifier.assert_not_called()
                self.assertFalse(verdict["valid"])
                self.assertEqual(verdict["failures"][0]["type"], termination)

    def test_nonzero_exit_with_valid_json_is_not_success(self):
        result = self.score(self.capture(exit_code=1))
        self.assertEqual(result["verdict"]["failures"][0]["type"], "nonzero_exit")

    def test_fixture_cannot_be_relabeled_as_live_execution(self):
        for changes in ({"mode": "live"}, {"execution_started": True}, {"limits_enforced": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.score({**self.capture(), **changes})
