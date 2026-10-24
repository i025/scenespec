"""Inventory observations cannot attest or enable candidate isolation."""
import json
import subprocess
import unittest
from unittest.mock import patch

from scene_rsi.meta.candidate_environment import inspect_candidate_environment, REQUIRED_BOUNDARIES


class TestCandidateEnvironment(unittest.TestCase):
    def test_inventory_never_starts_a_runtime_job_or_probe(self):
        with patch.object(subprocess, "run") as run, patch.object(subprocess, "Popen") as popen:
            report = inspect_candidate_environment()
        run.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(report["candidate_processes_started"], 0)
        self.assertFalse(report["candidate_execution_allowed"])
        self.assertEqual(set(report["boundaries"]), set(REQUIRED_BOUNDARIES))
        self.assertTrue(all(row["status"] == "not_verified" and not row["live_test_run"]
                            for row in report["boundaries"].values()))
        json.dumps(report, allow_nan=False)

    def test_runtime_and_scheduler_presence_never_authorize_execution(self):
        with patch("scene_rsi.meta.candidate_environment.shutil.which", side_effect=lambda name: "/bin/" + name):
            report = inspect_candidate_environment()
        self.assertTrue(report["observations"]["tools_on_path"]["apptainer"])
        self.assertTrue(report["observations"]["tools_on_path"]["srun"])
        self.assertFalse(report["backend_implemented"])
        self.assertFalse(report["candidate_execution_allowed"])
        self.assertEqual(report["administrator_approval"], "not_provided")

    def test_environment_inventory_does_not_export_secret_values(self):
        with patch.dict("os.environ", {"SLURM_JOB_ID": "PRIVATE_JOB_VALUE", "GITHUB_TOKEN": "PRIVATE_TOKEN"}):
            report = inspect_candidate_environment()
        text = json.dumps(report)
        self.assertIn("SLURM_JOB_ID", text)
        self.assertNotIn("PRIVATE_JOB_VALUE", text)
        self.assertNotIn("PRIVATE_TOKEN", text)
