"""Verify actual geometry/input changes, compatibility and invalid passage candidates."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from scene_io import export_npz, load_scene, rasterize, validate
from layout_eval import apply_layout, evaluate_candidates, evaluate_layout, LayoutError


class PassageTests(unittest.TestCase):
    def setUp(self):
        self.scene = load_scene(ROOT / "scenes/passage_optimization_example.json")

    def test_example_matches_schema_and_is_feasible(self):
        schema = json.loads((ROOT / "schema/scene_schema_v0.3.json").read_text(encoding="utf-8"))
        Draft7Validator(schema).validate(self.scene)
        self.assertEqual(validate(self.scene), [])

    def test_wider_passage_changes_real_geometry_preserves_crowd_and_fixed_structures(self):
        before = copy.deepcopy(self.scene)
        wider = apply_layout(self.scene, [{"passage_id": "P1", "width": 6}])
        a, b = rasterize(self.scene), rasterize(wider)
        self.assertEqual(self.scene, before)
        self.assertEqual(wider["meta"]["revision"], 2)
        self.assertGreater(b["walkable"].sum(), a["walkable"].sum())
        self.assertNotEqual(a["input_hash"], b["input_hash"])
        np.testing.assert_array_equal(a["rho0"], b["rho0"])
        np.testing.assert_array_equal(a["fixed_obstacle_mask"], b["fixed_obstacle_mask"])
        report = evaluate_layout(self.scene, [{"passage_id": "P1", "width": 6}])
        self.assertTrue(report["feasible"])
        self.assertEqual(report["passage_width_deltas"][0]["clear_area_delta_m2"], 8)
        self.assertEqual(report["variable_parameter_deltas"][1]["after_value"], 6)

    def test_x_direction_and_width_equal_envelope(self):
        p = self.scene["geometry"]["passages"][0]
        p.update(direction="x", length=4, envelope_width=8, width_limits=[2, 8])
        self.scene["variable_parameters"] = []
        baseline = rasterize(self.scene)
        p["width"] = 8
        after = rasterize(self.scene)
        self.assertTrue(baseline["passage_wall_mask"].any())
        self.assertFalse(after["passage_wall_mask"].any())

    def test_ranges_permissions_duplicate_changes_unknown_fields(self):
        for changes in ([{"passage_id": "P1", "width": 1}],
                        [{"passage_id": "P1", "width": float("nan")}],
                        [{"passage_id": "P1", "width": 5, "center": [15, 10]}],
                        [{"passage_id": "P1", "width": 5}, {"passage_id": "P1", "width": 6}],
                        [{"passage_id": "unknown", "width": 5}]):
            with self.subTest(changes=changes), self.assertRaises(LayoutError):
                apply_layout(self.scene, changes)
        self.scene["geometry"]["passages"][0]["adjustable"] = False
        with self.assertRaises(LayoutError):
            apply_layout(self.scene, [{"passage_id": "P1", "width": 5}])

    def test_cannot_cut_fixed_wall_or_overlap_structures(self):
        self.scene["geometry"]["walls"] = [[0, 10, 30, 10]]
        problems = validate(self.scene)
        self.assertTrue(any("固定隔墙" in p["msg"] for p in problems))
        self.scene["geometry"]["walls"] = []
        self.scene["geometry"]["obstacles"] = [{"name": "固定柱", "x": 14, "y": 9, "w": 1, "h": 1}]
        self.assertTrue(any("固定障碍冲突" in p["msg"] for p in validate(self.scene)))

    def test_cannot_hide_population_under_banks(self):
        self.scene["crowd"]["zones"][0]["polygon"] = [[2, 9], [5, 9], [5, 11], [2, 11]]
        self.assertTrue(any("初始人群" in p["msg"] for p in validate(self.scene)))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(RuntimeError):
            export_npz(self.scene, Path(tmp) / "bad.npz")

    def test_noop_and_subgrid_widths_deduplicate(self):
        report = evaluate_candidates(self.scene, [
            {"candidate_id": "same", "changes": [{"passage_id": "P1", "width": 4}]},
            {"candidate_id": "small", "changes": [{"passage_id": "P1", "width": 4.1}]},
            {"candidate_id": "wide", "changes": [{"passage_id": "P1", "width": 6}]},
        ])
        self.assertEqual(report["unique_solve_count"], 1)
        self.assertEqual(report["candidates"][1]["duplicate_of"], "baseline")

    def test_export_contains_passage_geometry_and_mask(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export_npz(self.scene, Path(tmp) / "scene.npz")
            with np.load(path, allow_pickle=False) as data:
                self.assertEqual(json.loads(str(data["passages_json"]))[0]["id"], "P1")
                self.assertTrue(data["passage_wall_mask"].any())
                self.assertAlmostEqual(float(data["rho0"].sum()) * float(data["dx"])**2, 80, places=5)


if __name__ == "__main__":
    unittest.main()
