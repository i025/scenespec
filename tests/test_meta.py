"""Exercise proposal privacy, Git provenance and fail-closed patch handling."""
from dataclasses import asdict
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from scene_rsi.benchmark.dataset import build_dataset, load_dataset
from scene_rsi.meta import (
    FixedMetaConfig, IsolationUnavailable, assert_candidate_execution_available,
    prepare_request, proposal_plan, record_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
AGENT = "scene_rsi/agent/fixed_agent.py"


def git(repo, *args):
    return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                                   stderr=subprocess.PIPE).decode().strip()


class TestFixedMetaFoundation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = TemporaryDirectory()
        cls.dataset = Path(cls.shared.name) / "dataset"
        build_dataset(cls.dataset, seed=8712, per_family=1)

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for name in ("scene_rsi/schema.py", "scene_rsi/geometry.py", AGENT, "scene_rsi/agent/__init__.py"):
            destination = self.repo / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        (self.repo / "hidden_test.json").write_text('{"canary":"NEVER_EXPORT_PRIVATE_TASK"}')
        git(self.repo, "init", "--quiet")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=Scene Test", "-c", "user.email=test@invalid", "commit", "-qm", "trusted parent")
        self.bundle = self.root / "bundle"
        self.audit = self.root / "audit"
        self.patch = self.root / "proposal.patch"

    def prepare(self, **kwargs):
        return prepare_request(self.repo, self.dataset, self.bundle, **kwargs)

    def write_patch(self, suffix="# candidate change\n", path=AGENT):
        original = (self.repo / path).read_text()
        diff = "diff --git a/{0} b/{0}\n".format(path) + "".join(difflib.unified_diff(
            original.splitlines(keepends=True), (original + suffix).splitlines(keepends=True),
            fromfile="a/" + path, tofile="b/" + path))
        self.patch.write_text(diff)
        return diff

    def record(self):
        return record_candidate(self.repo, self.bundle, self.patch, self.audit)

    def test_bundle_exports_only_dev_specs_and_allowlisted_sources(self):
        request = self.prepare()
        serialized = "\n".join(path.read_text() for path in self.bundle.rglob("*") if path.is_file())
        self.assertNotIn("NEVER_EXPORT_PRIVATE_TASK", serialized)
        self.assertFalse((self.bundle / ".git").exists())
        self.assertFalse(any("manifest" in path.name or "witness" in path.name for path in self.bundle.rglob("*")))
        dev = {task.spec.scene_id: asdict(task.spec) for task in load_dataset(self.dataset, "dev")}
        exported = {value["scene_id"]: value for value in
                    (json.loads(path.read_text()) for path in (self.bundle / "tasks").glob("*.json"))}
        # JSON round trip removes dataclass tuple/list representation differences.
        self.assertEqual(exported, json.loads(json.dumps(dev)))
        for split in ("val", "test"):
            for task in load_dataset(self.dataset, split):
                self.assertNotIn(task.spec.scene_id, serialized)
        self.assertEqual(request["task_count"], len(dev))

    def test_bundle_integrity_and_pinned_model_plan(self):
        self.prepare(config=FixedMetaConfig(model="explicit-experiment-model"))
        plan = proposal_plan(self.bundle)
        self.assertFalse(plan["execute"])
        self.assertIn("explicit-experiment-model", plan["argv"])
        self.assertIn("controller_repo", plan["required_boundary"]["forbidden_mounts"])
        (self.bundle / "source" / AGENT).write_text("tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            proposal_plan(self.bundle)

    def test_dirty_parent_cannot_be_prepared(self):
        (self.repo / "untracked.txt").write_text("not committed")
        with self.assertRaisesRegex(ValueError, "clean committed parent"):
            self.prepare()
        self.assertFalse(self.bundle.exists())

    def test_dev_report_sanitizes_details_and_rejects_hidden_or_wrong_parent(self):
        parent = git(self.repo, "rev-parse", "HEAD")
        task = load_dataset(self.dataset, "dev")[0]
        report = self.root / "report.json"
        payload = {"split": "test", "provenance": {"git": {"commit": parent, "dirty": False},
                   "dataset_manifest_sha256": hashlib.sha256((self.dataset / "manifest.json").read_bytes()).hexdigest()},
                   "results": [{"task_id": task.task_id, "spec_sha256": task.spec_sha256, "valid": False,
                                "secret": "NEVER_EXPORT_REPORT_METADATA",
                                "failures": [{"type": "missing_object", "message": "PRIVATE_EXCEPTION_PATH",
                                              "objects": [task.spec.required_objects[0].id]}]}]}
        report.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "only a dev report"):
            self.prepare(dev_report=report)
        payload["split"] = "dev"
        payload["provenance"]["git"]["commit"] = "0" * 40
        report.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "same clean parent"):
            self.prepare(dev_report=report)
        payload["provenance"]["git"]["commit"] = parent
        report.write_text(json.dumps(payload))
        self.prepare(dev_report=report)
        feedback = (self.bundle / "feedback.json").read_text()
        self.assertNotIn("NEVER_EXPORT_REPORT_METADATA", feedback)
        self.assertNotIn("PRIVATE_EXCEPTION_PATH", feedback)
        self.assertIn("missing_object", feedback)

    def test_dev_feedback_rejects_nested_metadata_and_non_dev_references(self):
        parent = git(self.repo, "rev-parse", "HEAD")
        task = load_dataset(self.dataset, "dev")[0]
        report = self.root / "report.json"
        payload = {"split": "dev", "provenance": {"git": {"commit": parent, "dirty": False},
                   "dataset_manifest_sha256": hashlib.sha256((self.dataset / "manifest.json").read_bytes()).hexdigest()},
                   "results": [{"task_id": task.task_id, "spec_sha256": task.spec_sha256, "valid": False}]}
        for failure in ({"type": {"hidden": "PRIVATE"}},
                        {"type": "missing_object", "objects": ["NON_DEV_OBJECT"]},
                        {"type": "constraint_violation", "constraint_index": {"hidden": "PRIVATE"}},
                        {"type": "constraint_violation", "constraint_index": True}):
            with self.subTest(failure=failure):
                payload["results"][0]["failures"] = [failure]
                report.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    self.prepare(dev_report=report)
                self.assertFalse(self.bundle.exists())

    def test_valid_patch_records_durable_reproducible_git_without_execution(self):
        self.prepare()
        marker = self.root / "executed.txt"
        self.write_patch(f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('unsafe')\n")
        parent = git(self.repo, "rev-parse", "HEAD")
        first, second = self.record(), self.record()
        self.assertEqual(first["status"], "accepted_static_only", first)
        self.assertEqual(first["candidate_git"]["commit"], second["candidate_git"]["commit"])
        self.assertFalse(marker.exists(), "static inspection must never execute candidate source")
        self.assertEqual(first["parent_git"]["commit"], parent)
        bare = Path(first["record_path"]).parent / "candidate.git"
        self.assertEqual(git(bare, "rev-parse", "refs/heads/parent"), parent)
        self.assertEqual(git(bare, "rev-parse", "refs/heads/candidate"), first["candidate_git"]["commit"])
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(first["resources"]["model_calls"], 0)
        self.assertIsNone(first["resources"]["model_tokens"])
        self.assertIsNone(first["resources"]["model_cost"])
        self.assertEqual(first["execution"]["status"], "not_run")
        self.assertEqual(first["test_results"]["dev"], "not_run")
        self.assertGreaterEqual(first["resources"]["wall_seconds"], 0)

    def test_protected_and_traversal_paths_are_rejected_and_raw_patch_archived(self):
        self.prepare()
        for path in ("scene_rsi/evaluator/verifier.py", "scene_rsi/meta/fixed.py",
                     "scene_rsi/agent/../../schema.py", "/tmp/escape.py"):
            with self.subTest(path=path):
                raw = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n".encode()
                self.patch.write_bytes(raw)
                record = self.record()
                self.assertEqual(record["status"], "rejected")
                self.assertEqual(record["checks"][-1]["name"], "patch_scope")
                self.assertEqual(record["patch_sha256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual((Path(record["record_path"]).parent / "proposal.patch").read_bytes(), raw)

    def test_symlink_mode_and_deletion_patches_rejected(self):
        self.prepare()
        for line in ("new file mode 120000", "old mode 100644\nnew mode 100755", "deleted file mode 100644"):
            with self.subTest(line=line):
                self.patch.write_text(f"diff --git a/{AGENT} b/{AGENT}\n{line}\n")
                record = self.record()
                self.assertEqual(record["status"], "rejected")
                self.assertEqual(record["checks"][-1]["name"], "patch_scope")

    def test_invalid_syntax_and_protected_imports_are_not_accepted(self):
        self.prepare()
        for suffix in ("\ndef broken(:\n", "\nfrom scene_rsi.evaluator.verifier import SceneVerifier\n"):
            with self.subTest(suffix=suffix):
                self.write_patch(suffix)
                record = self.record()
                self.assertEqual(record["status"], "rejected", record)
                self.assertEqual(record["checks"][-1]["name"], "syntax_and_import_policy")
                self.assertIsNone(record["candidate_git"])

    def test_failed_patch_application_is_recorded(self):
        self.prepare()
        self.patch.write_text(f"diff --git a/{AGENT} b/{AGENT}\n--- a/{AGENT}\n+++ b/{AGENT}\n"
                              "@@ -1 +1 @@\n-this line is not in the parent\n+replacement\n")
        record = self.record()
        self.assertEqual(record["status"], "rejected")
        self.assertEqual(record["checks"][-1]["name"], "git_apply")

    def test_budget_includes_rejected_and_interrupted_attempts(self):
        request = self.prepare(config=FixedMetaConfig(max_candidates=2))
        self.write_patch("\ndef syntax_error(:\n")
        self.assertEqual(self.record()["attempt_index"], 1)
        interrupted = self.audit / "candidate-interrupted"
        interrupted.mkdir()
        (interrupted / "reservation.json").write_text(json.dumps({"request_id": request["request_id"]}))
        self.write_patch()
        record = self.record()
        self.assertEqual(record["attempt_index"], 3)
        self.assertEqual(record["status"], "rejected")
        self.assertEqual(record["checks"][-1]["name"], "candidate_budget")

    def test_dirty_or_changed_parent_is_rejected_during_ingest(self):
        self.prepare()
        self.write_patch()
        (self.repo / "new_file.txt").write_text("dirty")
        record = self.record()
        self.assertEqual(record["status"], "rejected")
        self.assertEqual(record["checks"][-1]["name"], "parent_version")
        self.assertTrue(Path(record["record_path"]).is_file())

    def test_changing_audit_output_cannot_reset_request_candidate_budget(self):
        self.prepare(config=FixedMetaConfig(max_candidates=1))
        self.write_patch()
        self.assertEqual(self.record()["status"], "accepted_static_only")
        alternative = record_candidate(self.repo, self.bundle, self.patch, self.root / "other_audit")
        self.assertEqual(alternative["status"], "rejected")
        self.assertEqual(alternative["checks"][-1]["name"], "audit_root_binding")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_symlink_parent_source_is_refused(self):
        (self.repo / AGENT).unlink()
        (self.repo / AGENT).symlink_to("../schema.py")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=Scene Test", "-c", "user.email=test@invalid", "commit", "-qm", "unsafe parent")
        with self.assertRaisesRegex(ValueError, "regular non-executable"):
            self.prepare()

    def test_runtime_boundary_fails_closed(self):
        with self.assertRaises(IsolationUnavailable):
            assert_candidate_execution_available()

    def test_oversized_and_nonregular_patch_inputs_fail_without_unbounded_read(self):
        self.prepare()
        with self.patch.open("wb") as stream:
            stream.truncate(2 * 1048576)
        record = self.record()
        self.assertEqual(record["status"], "rejected")
        self.assertEqual(record["checks"][-1]["name"], "patch_read")
        self.assertFalse(record["patch_archive"]["complete"])
        self.assertIsNone(record["patch_sha256"])
        self.assertEqual(record["patch_archive"]["prefix_bytes"], 1048576)
        self.patch.unlink()
        os.mkfifo(self.patch)
        record = self.record()
        self.assertEqual(record["status"], "rejected")
        self.assertIn("regular file", record["checks"][-1]["message"])


if __name__ == "__main__":
    unittest.main()
