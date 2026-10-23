"""Public CLI integration and original-contract regression checks."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scene_rsi.integrity import check_original_contract

ROOT = Path(__file__).resolve().parents[1]


class TestCLI(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "scene_rsi", *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)

    def test_original_evaluation_contract_byte_identical(self):
        self.assertEqual(check_original_contract()["mismatches"], [])

    def test_dataset_evaluate_and_hidden_test_guard(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "dataset"
            built = self.cli("make-dataset", "--out", dataset, "--seed", 42, "--per-family", 1)
            self.assertEqual(built.returncode, 0, built.stderr)
            report = root / "dev.json"
            evaluated = self.cli("evaluate", "--dataset", dataset, "--split", "dev", "--out", report)
            self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
            self.assertEqual(json.loads(report.read_text())["n"], 2)
            hidden = self.cli("evaluate", "--dataset", dataset, "--split", "test", "--out", root / "test.json")
            self.assertNotEqual(hidden.returncode, 0)
            self.assertFalse((root / "test.json").exists())
            legacy = self.cli("benchmark", "--spec-dir", dataset / "test", "--out", root / "legacy.json")
            self.assertNotEqual(legacy.returncode, 0)
            self.assertFalse((root / "legacy.json").exists())
            hidden_spec = next((dataset / "test").glob("*.json"))
            generated = self.cli("generate", "--spec", hidden_spec, "--out", root / "generated")
            self.assertNotEqual(generated.returncode, 0)
            self.assertFalse((root / "generated").exists())

    def test_legacy_benchmark_and_empty_input(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            result = self.cli("benchmark", "--spec-dir", ROOT / "examples", "--out", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(output.read_text())
            self.assertEqual((data["n"], data["n_valid"]), (8, 8))
            empty = Path(tmp) / "empty"
            empty.mkdir()
            result = self.cli("benchmark", "--spec-dir", empty, "--out", output)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
