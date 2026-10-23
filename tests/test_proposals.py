"""P2A protocol and ledger unit tests, not live model/sandbox integration tests."""
from dataclasses import asdict, replace
import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scene_rsi.benchmark.dataset import build_dataset
from scene_rsi.meta.fixed import FixedMetaConfig, prepare_request, record_candidate
from scene_rsi.meta import proposals as proposals_module
from scene_rsi.meta.proposals import (
    MAX_FILE_BYTES, ProposalLimits, ingest_offline_proposal, parse_codex_usage,
    parse_proposal, request_proposal, reserve_proposal_attempt, safe_plan,
)

ROOT = Path(__file__).resolve().parents[1]
AGENT = "scene_rsi/agent/fixed_agent.py"


def git(repo, *args):
    return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                                   stderr=subprocess.PIPE).decode().strip()


class TestProposalParsers(unittest.TestCase):
    def test_exact_proposal_schema_and_byte_limit(self):
        value = {"patch": "a patch", "rationale": "offline fixture only"}
        self.assertEqual(parse_proposal(json.dumps(value).encode()), value)
        for bad in ({"patch": "x"}, {**value, "extra": True},
                    {"patch": " ", "rationale": "x"}, {"patch": [], "rationale": "x"},
                    {"patch": "x", "rationale": ""}, {"patch": "\0", "rationale": "x"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_proposal(json.dumps(bad).encode())
        with self.assertRaises(ValueError):
            parse_proposal(json.dumps(value).encode(), max_patch_bytes=1)
        for bad_limit in (True, 0, -1, MAX_FILE_BYTES + 1):
            with self.subTest(limit=bad_limit), self.assertRaises(ValueError):
                parse_proposal(json.dumps(value).encode(), max_patch_bytes=bad_limit)

    def test_duplicate_nonfinite_invalid_utf8_and_oversized_output(self):
        for data in (b'{"patch":"a","patch":"b","rationale":"x"}',
                     b'{"patch":NaN,"rationale":"x"}', b'{"patch":1e400,"rationale":"x"}',
                     b'{"patch":Infinity,"rationale":"x"}', b'\xff', b'{} trailing',
                     b' ' * (MAX_FILE_BYTES + 1)):
            with self.subTest(data=data[:70]), self.assertRaises(ValueError):
                parse_proposal(data)

    def test_known_usage_is_exact_and_absence_remains_unknown(self):
        data = (b'{"type":"thread.started","thread_id":"fixture"}\n'
                b'{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":25}}\n')
        self.assertEqual(parse_codex_usage(data), {"input_tokens": 100, "cached_input_tokens": 40,
                                                 "output_tokens": 25, "total_tokens": 125})
        for data in (b'', b'{"type":"turn.completed"}',
                     b'{"type":"turn.completed","usage":null}',
                     b'{"type":"turn.completed","usage":{}}',
                     b'{"type":"turn.failed","error":{"message":"fixture"}}'):
            with self.subTest(data=data):
                self.assertIsNone(parse_codex_usage(data))
        partial = parse_codex_usage(b'{"type":"turn.completed","usage":{"output_tokens":0}}')
        self.assertEqual(partial, {"input_tokens": None, "cached_input_tokens": None,
                                   "output_tokens": 0, "total_tokens": None})

    def test_usage_rejects_malformed_counts_and_terminal_contradictions(self):
        for usage in ({"input_tokens": True}, {"output_tokens": -1}, {"input_tokens": 1.5},
                      {"input_tokens": 2, "cached_input_tokens": 3}, [], "unknown"):
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                parse_codex_usage(json.dumps({"type": "turn.completed", "usage": usage}).encode())
        completed = b'{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}'
        for terminal in (completed, b'{"type":"turn.failed"}', b'{"type":"error"}'):
            for data in (terminal + b'\n' + completed, completed + b'\n' + terminal):
                with self.subTest(data=data), self.assertRaises(ValueError):
                    parse_codex_usage(data)
        for data in (b'{"type":"turn.completed","usage":{"input_tokens":1,"input_tokens":2}}',
                     b'{"type":"item.completed","unknown":1e400}', b'[]', b'{"type":true}'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_codex_usage(data)

    def test_limits_reject_bool_zero_noninteger_and_unbounded_values(self):
        defaults = ProposalLimits()
        defaults.validate()
        for name in asdict(defaults):
            for invalid in (True, 0, 1.5, 10 ** 20):
                with self.subTest(name=name, value=invalid), self.assertRaises(ValueError):
                    replace(defaults, **{name: invalid}).validate()
        with self.assertRaises(ValueError):
            replace(defaults, total_reserved_tokens=1).validate()
        with self.assertRaises(ValueError):
            replace(defaults, total_reserved_wall_seconds=1).validate()


class TestProposalController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = TemporaryDirectory()
        cls.dataset = Path(cls.shared.name) / "dataset"
        build_dataset(cls.dataset, seed=6501, per_family=1)

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo, self.bundle, self.audit = (self.root / name for name in ("repo", "bundle", "audit"))
        self.repo.mkdir()
        for name in ("scene_rsi/schema.py", "scene_rsi/geometry.py", AGENT, "scene_rsi/agent/__init__.py"):
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        git(self.repo, "init", "--quiet")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=Offline Test", "-c", "user.email=test@invalid", "commit", "-qm", "parent")

    def prepare(self, count=3, model="offline-fixture-model"):
        return prepare_request(self.repo, self.dataset, self.bundle,
                               config=FixedMetaConfig(model=model, max_candidates=count))

    def patch_file(self):
        original = (self.repo / AGENT).read_text()
        diff = f"diff --git a/{AGENT} b/{AGENT}\n" + "".join(difflib.unified_diff(
            original.splitlines(keepends=True), (original + "\n# inert offline fixture\n").splitlines(keepends=True),
            fromfile="a/" + AGENT, tofile="b/" + AGENT))
        path = self.root / "input.patch"
        path.write_text(diff)
        return path

    def test_registered_plan_has_no_live_or_credential_switch(self):
        self.prepare()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "PRIVATE_CREDENTIAL_CANARY",
                                     "CODEX_HOME": "/PRIVATE_HOME_CANARY"}):
            plan = safe_plan(self.repo, self.bundle)
        self.assertFalse(plan["execute"])
        self.assertFalse(plan["limits_enforced"])
        self.assertEqual(plan["model"], "offline-fixture-model")
        self.assertNotIn("PRIVATE_CREDENTIAL_CANARY", json.dumps(plan))
        self.assertNotIn("PRIVATE_HOME_CANARY", json.dumps(plan))
        self.assertNotIn(str(self.repo), json.dumps(plan))
        with self.assertRaises(TypeError):
            request_proposal(self.repo, self.bundle, self.audit, backend_available=True)

    def test_missing_model_and_unregistered_bundle_are_rejected(self):
        request = self.prepare(model=None)
        with self.assertRaisesRegex(ValueError, "pinned model"):
            safe_plan(self.repo, self.bundle)
        registration = self.repo / ".git/scene-rsi-meta/prepared" / (request["request_id"] + ".json")
        registration.unlink()
        with self.assertRaisesRegex(ValueError, "not registered"):
            safe_plan(self.repo, self.bundle)

    def test_symlink_ancestor_and_extra_empty_directory_are_rejected(self):
        self.prepare()
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            safe_plan(self.repo, alias / "bundle")
        (self.bundle / "secret-empty-directory").mkdir()
        with self.assertRaisesRegex(ValueError, "empty directories"):
            safe_plan(self.repo, self.bundle)

    def test_p1_static_attempt_and_p2_reservation_share_one_quota(self):
        self.prepare(count=2)
        patch_path = self.patch_file()
        first = record_candidate(self.repo, self.bundle, patch_path, self.audit)
        self.assertEqual(first["attempt_index"], 1)
        reserved = reserve_proposal_attempt(self.repo, self.bundle, self.audit, source="offline_fixture")
        second = record_candidate(self.repo, Path(reserved["directory"]) / "request_snapshot", patch_path,
                                  self.audit, reserved_candidate_id=reserved["candidate_id"])
        self.assertEqual(second["attempt_index"], 2)
        self.assertEqual(second["status"], "accepted_static_only", second)
        denied = request_proposal(self.repo, self.bundle, self.audit)
        self.assertEqual(denied["attempt_index"], 3)
        self.assertEqual(denied["reason"], "candidate_budget_exhausted")
        self.assertEqual(len(list(self.audit.glob("candidate-*/reservation.json"))), 3)

    def test_interrupted_p2_reservation_consumes_p1_quota_and_cannot_be_replayed(self):
        self.prepare(count=2)
        reserved = reserve_proposal_attempt(self.repo, self.bundle, self.audit, source="offline_fixture")
        patch_path = self.patch_file()
        ordinary = record_candidate(self.repo, self.bundle, patch_path, self.audit)
        self.assertEqual(ordinary["attempt_index"], 2)
        self.assertEqual(request_proposal(self.repo, self.bundle, self.audit)["reason"],
                         "candidate_budget_exhausted")
        completed = record_candidate(self.repo, self.bundle, patch_path, self.audit,
                                     reserved_candidate_id=reserved["candidate_id"])
        self.assertEqual(completed["attempt_index"], 1)
        self.assertEqual(completed["status"], "accepted_static_only")
        with self.assertRaisesRegex(ValueError, "not available"):
            record_candidate(self.repo, self.bundle, patch_path, self.audit,
                             reserved_candidate_id=reserved["candidate_id"])

    def test_snapshot_write_failure_has_durable_error_and_consumes_quota(self):
        self.prepare(count=1)
        original = proposals_module._write_snapshot

        def fail_candidate_snapshot(destination, payloads):
            if destination.name == "request_snapshot":
                raise OSError("injected candidate snapshot storage failure")
            return original(destination, payloads)

        with patch.object(proposals_module, "_write_snapshot", side_effect=fail_candidate_snapshot):
            reserved = reserve_proposal_attempt(self.repo, self.bundle, self.audit, source="offline_fixture")
        self.assertEqual(reserved["status"], "preparation_failed")
        failed = reserved["final_record"]
        self.assertEqual(failed["reason"], "request_snapshot_failed")
        self.assertEqual(failed["resources"]["model_calls"], 0)
        saved = json.loads(Path(failed["proposal_record_path"]).read_text())
        self.assertIn("injected", saved["error"]["message"])
        with self.assertRaisesRegex(ValueError, "not available"):
            record_candidate(self.repo, self.bundle, self.patch_file(), self.audit,
                             reserved_candidate_id=reserved["candidate_id"])
        self.assertEqual(request_proposal(self.repo, self.bundle, self.audit)["reason"],
                         "candidate_budget_exhausted")

    def test_immutable_snapshot_survives_original_bundle_edit(self):
        self.prepare()
        reserved = reserve_proposal_attempt(self.repo, self.bundle, self.audit, source="offline_fixture")
        snapshot = Path(reserved["directory"]) / "request_snapshot"
        original = (snapshot / "source" / AGENT).read_bytes()
        self.assertEqual((snapshot / "source" / AGENT).stat().st_mode & 0o222, 0)
        (self.bundle / "source" / AGENT).write_text("edited after reservation")
        self.assertEqual((snapshot / "source" / AGENT).read_bytes(), original)
        result = record_candidate(self.repo, snapshot, self.patch_file(), self.audit,
                                  reserved_candidate_id=reserved["candidate_id"])
        self.assertEqual(result["status"], "accepted_static_only", result)
        with self.assertRaises(ValueError):
            safe_plan(self.repo, self.bundle)

    def test_limits_are_fixed_and_token_reservation_is_conservative(self):
        self.prepare()
        limits = ProposalLimits(input_tokens=1, output_tokens=1, total_reserved_tokens=2)
        first = request_proposal(self.repo, self.bundle, self.audit, limits=limits)
        second = request_proposal(self.repo, self.bundle, self.audit, limits=limits)
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(second["reason"], "declared_token_budget_exhausted")
        with self.assertRaisesRegex(ValueError, "already fixed"):
            request_proposal(self.repo, self.bundle, self.audit,
                             limits=replace(limits, total_reserved_tokens=4))

    def test_wall_reservations_limit_attempts_even_without_a_model_call(self):
        self.prepare()
        limits = ProposalLimits(wall_seconds=1, total_reserved_wall_seconds=1)
        request_proposal(self.repo, self.bundle, self.audit, limits=limits)
        denied = request_proposal(self.repo, self.bundle, self.audit, limits=limits)
        self.assertEqual(denied["reason"], "declared_wall_budget_exhausted")
        self.assertEqual(denied["resources"]["model_calls"], 0)
        self.assertIsNone(denied["resources"]["model_tokens"])

    def test_offline_usage_is_separate_from_actual_model_costs(self):
        self.prepare(count=1)
        proposal, events = self.root / "output.json", self.root / "events.jsonl"
        proposal.write_text(json.dumps({"patch": self.patch_file().read_text(), "rationale": "inert fixture"}))
        events.write_text('{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":3}}\n')
        result = ingest_offline_proposal(self.repo, self.bundle, proposal, self.audit, events=events)
        self.assertEqual(result["status"], "accepted_static_only", result)
        self.assertEqual(result["source"], "offline_fixture")
        self.assertEqual(result["fixture_usage"]["total_tokens"], 13)
        self.assertEqual(result["resources"]["model_calls"], 0)
        self.assertIsNone(result["resources"]["model_tokens"])
        self.assertIsNone(result["resources"]["model_cost"])
        self.assertGreaterEqual(result["resources"]["controller_cpu_seconds"], 0)
        self.assertEqual(len(list(self.audit.glob("candidate-*/reservation.json"))), 1)

    def test_malformed_output_keeps_raw_events_and_consumes_attempt(self):
        self.prepare(count=1)
        proposal, events = self.root / "bad.json", self.root / "events.jsonl"
        proposal.write_text("not json")
        events.write_text('{"type":"turn.failed"}\n')
        result = ingest_offline_proposal(self.repo, self.bundle, proposal, self.audit, events=events)
        self.assertEqual(result["reason"], "invalid_offline_fixture")
        directory = Path(result["proposal_record_path"]).parent
        self.assertEqual((directory / "model-output.json").read_bytes(), proposal.read_bytes())
        self.assertEqual((directory / "codex-events.jsonl").read_bytes(), events.read_bytes())
        self.assertIsNone(result["candidate_record"])
        denied = request_proposal(self.repo, self.bundle, self.audit)
        self.assertEqual(denied["reason"], "candidate_budget_exhausted")

    def test_oversized_output_archives_bounded_prefix_with_no_full_hash(self):
        self.prepare()
        proposal = self.root / "large.json"
        proposal.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        result = ingest_offline_proposal(self.repo, self.bundle, proposal, self.audit)
        self.assertEqual(result["reason"], "invalid_offline_fixture")
        archive = result["archives"]["model-output.json"]
        self.assertFalse(archive["complete"])
        self.assertEqual(archive["prefix_bytes"], MAX_FILE_BYTES)
        self.assertIsNone(archive["full_sha256"])


if __name__ == "__main__":
    unittest.main()
