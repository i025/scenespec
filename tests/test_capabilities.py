"""Offline inventory tests; these are not OS isolation regression evidence."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from scene_rsi.meta import capabilities as cap


class TestOfflineCapabilityInventory(unittest.TestCase):
    def test_default_inventory_never_executes_any_program(self):
        with patch.object(cap.subprocess, "run") as run:
            report = cap.probe_capabilities()
        run.assert_not_called()
        self.assertTrue(report["offline_evidence_only"])
        self.assertFalse(report["live_allowed"])
        self.assertEqual(report["observations"]["namespace_probe"]["status"], "not_requested")
        json.dumps(report, allow_nan=False)

    def test_mocked_all_features_cannot_enable_live_execution(self):
        with patch.object(cap.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
             patch.object(cap, "_cgroups", return_value={"mounts": [{"read_only": False}],
                                                        "writable_mount_observed": True,
                                                        "delegation_verified": False}), \
             patch.object(cap, "_namespace_probe", return_value={"requested": True, "success": True}):
            report = cap.probe_capabilities(namespace_probe=True)
        for key in ("backend_implemented", "credential_broker_implemented", "controller_safety_tests_passed",
                    "live_allowed", "candidate_execution_allowed", "proposal_invocation_allowed"):
            self.assertIs(report[key], False, key)
        codes = {item["code"] for item in report["blockers"]}
        self.assertIn("backend_not_implemented", codes)
        self.assertIn("credential_broker_not_implemented", codes)
        self.assertIn("controller_safety_tests_not_passed", codes)

    def test_subordinate_inventory_exposes_only_current_user_boolean_count(self):
        content = "private_other_user:100000:65536\ncurrent:200000:65536\n1005:300000:65536\n"
        with patch.object(cap, "_read", return_value=(content, None)):
            result = cap._subordinate_entries("/etc/subuid", "current", 1005)
        self.assertEqual(result["current_user_entry_count"], 2)
        self.assertTrue(result["current_user_has_entries"])
        serialized = json.dumps(result)
        for private in ("private_other_user", "100000", "200000", "300000", "65536"):
            self.assertNotIn(private, serialized)

    def test_missing_subordinate_file_is_unknown_not_false_claim(self):
        with patch.object(cap, "_read", return_value=(None, "FileNotFoundError")):
            result = cap._subordinate_entries("/etc/subuid", "current", 1005)
        self.assertIsNone(result["current_user_has_entries"])
        self.assertIsNone(result["current_user_entry_count"])

    def test_mountinfo_observes_read_only_hybrid_cgroups(self):
        content = ("1 0 0:1 / /sys/fs/cgroup/memory ro,nosuid - cgroup cgroup rw,memory\n"
                   "2 0 0:2 / /sys/fs/cgroup/unified ro,nosuid - cgroup2 cgroup rw\n")
        def read(path, limit=131072):
            return (content if str(path) == "/proc/self/mountinfo" else "", None)
        with patch.object(cap, "_read", side_effect=read), patch.object(cap.os, "access", return_value=False):
            result = cap._cgroups()
        self.assertEqual(len(result["mounts"]), 2)
        self.assertFalse(result["writable_mount_observed"])
        self.assertFalse(result["delegation_verified"])
        self.assertEqual(result["mounts"][1]["cgroup.controllers"]["values"], [])

    def test_namespace_probe_command_is_literal_bounded_system_noop(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch.object(cap, "_trusted_binary", return_value=True), \
             patch.object(cap.subprocess, "run", return_value=completed) as run:
            result = cap._namespace_probe("/usr/bin/unshare")
        args, kwargs = run.call_args
        self.assertEqual(args[0], [str(Path("/usr/bin/unshare").resolve()), "--user", "--map-root-user",
                                  "--mount", "--net", "--pid", "--fork", "/bin/true"])
        self.assertEqual(kwargs["timeout"], cap.PROBE_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["cwd"], "/")
        self.assertEqual(set(kwargs["env"]), {"PATH", "LANG", "LC_ALL"})
        self.assertTrue(result["success"])

    def test_namespace_probe_rejects_untrusted_path_without_running(self):
        with patch.object(cap, "_trusted_binary", return_value=False), patch.object(cap.subprocess, "run") as run:
            result = cap._namespace_probe("/tmp/untrusted/unshare")
        run.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "trusted_system_unshare_unavailable")

    def test_namespace_probe_timeout_stays_fail_closed(self):
        timeout = subprocess.TimeoutExpired(["unshare"], 5, output=b"x" * 5000, stderr=b"timeout")
        with patch.object(cap, "_trusted_binary", return_value=True), \
             patch.object(cap.subprocess, "run", side_effect=timeout):
            result = cap._namespace_probe("/usr/bin/unshare")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "timeout")
        self.assertLessEqual(len(result["stdout"].encode()), cap.PROBE_OUTPUT_BYTES)

    def test_namespace_probe_error_stays_fail_closed(self):
        with patch.object(cap, "_trusted_binary", return_value=True), \
             patch.object(cap.subprocess, "run", side_effect=PermissionError("private detail")):
            result = cap._namespace_probe("/usr/bin/unshare")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "PermissionError")
        self.assertNotIn("private detail", json.dumps(result))

    def test_namespace_probe_records_nonzero_exit_and_caps_output(self):
        completed = subprocess.CompletedProcess([], 1, stdout=b"a" * 5000, stderr=b"b" * 6000)
        with patch.object(cap, "_trusted_binary", return_value=True), \
             patch.object(cap.subprocess, "run", return_value=completed):
            result = cap._namespace_probe("/usr/bin/unshare")
        self.assertFalse(result["success"])
        self.assertEqual(result["returncode"], 1)
        self.assertTrue(result["output_truncated"])
        self.assertEqual(len(result["stdout"]), cap.PROBE_OUTPUT_BYTES)
        self.assertEqual(len(result["stderr"]), cap.PROBE_OUTPUT_BYTES)

    def test_only_explicit_boolean_enables_namespace_probe(self):
        with patch.object(cap.subprocess, "run") as run:
            for value in (1, "true", None):
                with self.assertRaises(ValueError):
                    cap.probe_capabilities(value)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
