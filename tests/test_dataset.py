"""Generator feasibility, independent split structure, and dataset integrity tests."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scene_rsi.benchmark import dataset
from scene_rsi.evaluator.verifier import SceneVerifier
from scene_rsi.schema import SceneState


def topology(spec):
    """Independent unlabeled component/degree signature (sufficient for our motifs)."""
    edges = [(c.reference, c.subject) for c in spec.constraints if c.type == "in_front_of"]
    remaining = {obj.id for obj in spec.required_objects}
    components = []
    while remaining:
        component = {next(iter(remaining))}
        while True:
            enlarged = component | {n for a, b in edges if a in component or b in component for n in (a, b)}
            if component == enlarged:
                break
            component = enlarged
        remaining -= component
        components.append(tuple(sorted((sum(b == node for a, b in edges),
                                         sum(a == node for a, b in edges)) for node in component)))
    return tuple(sorted(components))


class TestConstructiveGenerator(unittest.TestCase):
    def test_many_seeds_have_independently_valid_witnesses(self):
        rotations = set()
        for family in dataset.FAMILIES:
            for seed in (0, 1, 7, 42, 101, 20261023, -19):
                for index in (0, 3):
                    with self.subTest(family=family, seed=seed, index=index):
                        spec, witness = dataset.generate_task(family, seed, index)
                        verdict = SceneVerifier().verify(spec, witness)
                        self.assertTrue(verdict["valid"], verdict["failures"])
                        self.assertGreaterEqual(len(spec.required_objects), 7)
                        self.assertGreaterEqual(len(spec.access_paths), 2)
                        self.assertGreaterEqual(sum(c.type == "keep_clear" for c in spec.constraints), 3)
                        self.assertGreaterEqual(len({c.wall for c in spec.constraints if c.type == "near_wall"}), 2)
                        rotations.update(obj.yaw_deg for obj in witness.objects)
        self.assertEqual(rotations, {0, 90, 180, 270})

    def test_split_relations_and_asset_combinations_are_disjoint(self):
        graphs, assets = {}, {}
        for split, families in dataset.SPLIT_FAMILIES.items():
            self.assertGreaterEqual(len(families), 2)
            graphs[split], assets[split] = set(), set()
            for family in families:
                spec, _ = dataset.generate_task(family, 19, 0)
                graphs[split].add(topology(spec))
                assets[split].update(spec.assets)
        for left, right in (("dev", "val"), ("dev", "test"), ("val", "test")):
            self.assertTrue(graphs[left].isdisjoint(graphs[right]))
            self.assertTrue(assets[left].isdisjoint(assets[right]))

    def test_generation_is_deterministic_and_invalid_arguments_fail(self):
        a = dataset.generate_task("shared_gallery", 10, 2)
        b = dataset.generate_task("shared_gallery", 10, 2)
        self.assertEqual(a, b)
        self.assertNotEqual(a, dataset.generate_task("shared_gallery", 11, 2))
        for args in (("unknown", 1, 0), ("corner_chains", 1, -1),
                     ("corner_chains", True, 0), ("corner_chains", 1, 1.5)):
            with self.assertRaises(ValueError):
                dataset.generate_task(*args)


class TestDatasetFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "dataset"
        self.manifest = dataset.build_dataset(self.root, seed=27, per_family=1)

    def edit_manifest(self, edit):
        value = json.loads((self.root / "manifest.json").read_text())
        edit(value)
        value.pop("dataset_id")
        value["dataset_id"] = hashlib.sha256(dataset.canonical_json(value)).hexdigest()
        (self.root / "manifest.json").write_bytes(dataset.canonical_json(value))

    def test_byte_identical_rebuild_and_private_witness_roundtrip(self):
        other = Path(self.temp.name) / "repeat"
        self.assertEqual(self.manifest, dataset.build_dataset(other, seed=27, per_family=1))
        content = []
        for name in self.root.rglob("*.json"):
            self.assertEqual(name.read_bytes(), (other / name.relative_to(self.root)).read_bytes())
        for split in ("dev", "val", "test"):
            tasks = dataset.load_dataset(self.root, split)
            self.assertEqual(len(tasks), 2)
            for task in tasks:
                self.assertEqual(task.task_id, task.spec.scene_id)
                entry = next(item for item in self.manifest["tasks"] if item["task_id"] == task.task_id)
                witness = SceneState.load(self.root / entry["witness_path"])
                self.assertTrue(SceneVerifier().verify(task.spec, witness)["valid"])
                self.assertEqual(entry["spec_sha256"], task.spec_sha256)
                content.append(entry["content_sha256"])
        self.assertEqual(len(content), len(set(content)))
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / "private").stat().st_mode & 0o777, 0o700)

    def test_loading_dev_never_reads_other_specs_or_witnesses(self):
        original = dataset._safe_read
        paths = []
        def checked_read(root, relative):
            paths.append(relative)
            if relative != "manifest.json" and not relative.startswith("dev/"):
                self.fail("dev loader tried to read hidden data")
            return original(root, relative)
        with patch.object(dataset, "_safe_read", side_effect=checked_read):
            tasks = dataset.load_dataset(self.root, "dev")
        self.assertEqual(len(paths), 3)
        self.assertEqual(len(tasks), 2)
        for task in tasks:
            self.assertEqual(set(asdict(task)), {"spec", "family", "task_id", "spec_sha256"})

    def test_refuses_existing_directory(self):
        with self.assertRaises(FileExistsError):
            dataset.build_dataset(self.root)

    def test_invalid_split_and_manifest_corruption_fail(self):
        with self.assertRaises(ValueError):
            dataset.load_dataset(self.root, "train")
        value = json.loads((self.root / "manifest.json").read_text())
        value["seed"] += 1
        (self.root / "manifest.json").write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "manifest integrity"):
            dataset.load_dataset(self.root, "dev")

    def test_changed_spec_is_detected(self):
        entry = self.manifest["tasks"][0]
        path = self.root / entry["spec_path"]
        value = json.loads(path.read_text())
        value["room"]["width"] += .5
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "spec integrity"):
            dataset.load_dataset(self.root, "dev")

    def test_rejects_symlink_even_when_contents_are_correct(self):
        entry = self.manifest["tasks"][0]
        path = self.root / entry["spec_path"]
        saved = Path(self.temp.name) / "redirect.json"
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, "symlink"):
            dataset.load_dataset(self.root, "dev")

    def test_rejects_root_symlink(self):
        redirect = Path(self.temp.name) / "redirect"
        redirect.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            dataset.load_dataset(redirect, "dev")

    def test_rejects_path_traversal_even_with_rehashed_manifest(self):
        self.edit_manifest(lambda m: m["tasks"][0].update(spec_path="../outside.json"))
        with self.assertRaisesRegex(ValueError, "spec path"):
            dataset.load_dataset(self.root, "dev")

    def test_rejects_empty_and_duplicate_split_membership(self):
        self.edit_manifest(lambda m: m["splits"].update(dev=[]))
        with self.assertRaisesRegex(ValueError, "split membership"):
            dataset.load_dataset(self.root, "dev")

    def test_private_generation_not_exposed_in_spec_json(self):
        for task in dataset.load_dataset(self.root, "dev"):
            value = json.loads((self.root / "dev" / (task.task_id + ".json")).read_text())
            self.assertEqual(set(value), {"scene_id", "room", "required_objects", "assets",
                                          "regions", "constraints", "access_paths"})
            self.assertFalse(any(key in value for key in ("seed", "witness", "solution")))


if __name__ == "__main__":
    unittest.main()
