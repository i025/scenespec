"""Run with: python -m unittest discover -s tests -v"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scene_rsi.agent.fixed_agent import FixedSceneAgent
from scene_rsi.evaluator.verifier import SceneVerifier
from scene_rsi.export import export_svg, export_obj
from scene_rsi.schema import SceneSpec, SceneState

ROOT = Path(__file__).resolve().parents[1]


def spec_for(objects, constraints=None, regions=None, paths=None, room=None):
    return SceneSpec.from_dict({
        "scene_id": "test_case",
        "room": room or {"width": 4, "depth": 4, "height": 3},
        "assets": {asset_id: dims for asset_id, dims in objects.values()},
        "required_objects": [{"id": id_, "asset": asset_id} for id_, (asset_id, _) in objects.items()],
        "constraints": constraints or [], "regions": regions or [], "access_paths": paths or []
    })


def state(*objects):
    return SceneState.from_dict({"scene_id": "test_case", "objects": list(objects)})


def placed(id_, asset, x, y, yaw=0):
    return {"id": id_, "asset": asset, "x": x, "y": y, "yaw_deg": yaw}


def failure_types(result):
    return {f["type"] for f in result["failures"]}


class TestSchemaAndVerifier(unittest.TestCase):
    def setUp(self):
        self.verifier = SceneVerifier(grid_step=0.1)
        self.box = ("box", {"width": 1.0, "depth": 1.0, "height": 0.7})

    def test_clean_scene(self):
        spec = spec_for({"a": self.box})
        result = self.verifier.verify(spec, state(placed("a", "box", 2, 2)))
        self.assertTrue(result["valid"])
        self.assertEqual(result["failures"], [])

    def test_missing_object(self):
        spec = spec_for({"a": self.box})
        result = self.verifier.verify(spec, state())
        self.assertFalse(result["valid"])
        self.assertIn("missing_object", failure_types(result))

    def test_wrong_asset(self):
        spec = spec_for({"a": self.box})
        result = self.verifier.verify(spec, state(placed("a", "fake", 2, 2)))
        self.assertIn("wrong_asset", failure_types(result))

    def test_collision(self):
        spec = spec_for({"a": self.box, "b": self.box})
        result = self.verifier.verify(spec, state(placed("a", "box", 2, 2), placed("b", "box", 2.3, 2.3)))
        self.assertIn("collision", failure_types(result))

    def test_out_of_bounds(self):
        spec = spec_for({"a": self.box})
        result = self.verifier.verify(spec, state(placed("a", "box", 0.2, 2)))
        self.assertIn("out_of_bounds", failure_types(result))

    def test_near_wall_checks_object_face_not_centre(self):
        spec = spec_for({"a": ("thin", {"width": 1, "depth": .5, "height": .7})},
                        constraints=[{"type": "near_wall", "subject": "a", "wall": "north", "max_gap": .2}])
        self.assertTrue(self.verifier.verify(spec, state(placed("a", "thin", 2, 3.65)))["valid"])
        self.assertIn("constraint_violation", failure_types(self.verifier.verify(spec, state(placed("a", "thin", 2, 2)))))

    def test_in_front_uses_reference_orientation(self):
        spec = spec_for({"desk": ("desk", {"width": 1, "depth": .5, "height": .7}),
                         "chair": ("chair", {"width": .4, "depth": .4, "height": .7})},
                        constraints=[{"type": "in_front_of", "subject": "chair", "reference": "desk", "max_gap": .3}])
        # Desk yaw=90 means its +Y-facing front rotates to world -X.
        good = state(placed("desk", "desk", 2, 2, 90), placed("chair", "chair", 1.35, 2, 270))
        bad = state(placed("desk", "desk", 2, 2, 90), placed("chair", "chair", 2.65, 2, 270))
        self.assertTrue(self.verifier.verify(spec, good)["valid"])
        self.assertIn("constraint_violation", failure_types(self.verifier.verify(spec, bad)))

    def test_keep_clear(self):
        spec = spec_for({"a": self.box},
                        constraints=[{"type": "keep_clear", "region": "door"}],
                        regions=[{"id": "door", "x": .1, "y": .1, "width": .9, "depth": .9}])
        result = self.verifier.verify(spec, state(placed("a", "box", .6, .6)))
        self.assertIn("constraint_violation", failure_types(result))

    def test_path_blocked_by_full_width_barrier(self):
        spec = spec_for({
            "barrier": ("wall", {"width": .35, "depth": 3, "height": 1.5}),
            "goal": ("goal", {"width": .5, "depth": .5, "height": .5})},
            room={"width": 4, "depth": 3, "height": 3},
            constraints=[{"type": "keep_clear", "region": "door"}],
            regions=[{"id": "door", "x": .15, "y": 1.15, "width": .8, "depth": .7}],
            paths=[{"from_region": "door", "to_object": "goal", "clearance": .15, "interaction_distance": .7}])
        blocked = state(placed("barrier", "wall", 2, 1.5), placed("goal", "goal", 3.3, 1.5))
        result = self.verifier.verify(spec, blocked)
        self.assertIn("blocked_path", failure_types(result))
        self.assertNotIn("collision", failure_types(result))

    def test_invalid_scene_id(self):
        spec = spec_for({"a": self.box})
        wrong_state = SceneState.from_dict({"scene_id": "wrong_scene", "objects": [placed("a", "box", 2, 2)]})
        self.assertIn("scene_id_mismatch", failure_types(self.verifier.verify(spec, wrong_state)))

    def test_duplicate_id(self):
        spec = spec_for({"a": self.box})
        result = self.verifier.verify(spec, state(placed("a", "box", 2, 2), placed("a", "box", 2, 2)))
        self.assertIn("duplicate_object", failure_types(result))

    def test_invalid_yaw(self):
        spec = spec_for({"a": self.box})
        result = self.verifier.verify(spec, state(placed("a", "box", 2, 2, 45)))
        self.assertIn("invalid_transform", failure_types(result))

    def test_spec_rejects_unknown_constraint_subject(self):
        with self.assertRaises(ValueError):
            spec_for({"a": self.box}, constraints=[{"type": "near_wall", "subject": "ghost", "wall": "north"}])


class TestFixedAgent(unittest.TestCase):
    def test_example_scenes_pass_hard_constraints(self):
        for filename in sorted((ROOT / "examples").glob("*.json")):
            with self.subTest(scene=filename.name):
                spec = SceneSpec.load(filename)
                generated = FixedSceneAgent().generate(spec)
                verdict = SceneVerifier().verify(spec, generated)
                self.assertTrue(verdict["valid"], verdict["failures"])

    def test_deterministic(self):
        spec = SceneSpec.load(ROOT / "examples" / "bedroom_001.json")
        self.assertEqual(FixedSceneAgent().generate(spec).to_dict(), FixedSceneAgent().generate(spec).to_dict())

    def test_feasible_challenge_not_solved_by_fixed_agent(self):
        spec = SceneSpec.load(ROOT / "benchmarks" / "dev" / "corner_desk_challenge.json")
        baseline = FixedSceneAgent().generate(spec)
        valid_reference = SceneState.load(ROOT / "benchmarks" / "golden" / "corner_desk_challenge.state.json")
        verifier = SceneVerifier()
        self.assertFalse(verifier.verify(spec, baseline)["valid"])
        self.assertTrue(verifier.verify(spec, valid_reference)["valid"])

    def test_roundtrip_and_export(self):
        spec = SceneSpec.load(ROOT / "examples" / "bedroom_001.json")
        generated = FixedSceneAgent().generate(spec)
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            generated.save(directory / "state.json")
            self.assertEqual(SceneState.load(directory / "state.json"), generated)
            export_svg(spec, generated, directory / "preview.svg")
            export_obj(spec, generated, directory / "scene.obj")
            for name in ("preview.svg", "scene.obj", "scene.mtl"):
                self.assertGreater((directory / name).stat().st_size, 10)


if __name__ == "__main__":
    unittest.main()
