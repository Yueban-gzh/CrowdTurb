# -*- coding: utf-8 -*-
"""A 模块回归测试：v0.1 兼容、参数化设施、几何校验、掩码哈希与候选去重。"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from geometry import (  # noqa: E402
    convex_polygons_overlap,
    facility_polygon,
    polygon_inside_polygon,
)
from layout_eval import (  # noqa: E402
    LayoutError,
    apply_layout,
    evaluate_candidates,
    evaluate_layout,
)
from scene_io import (  # noqa: E402
    export_npz,
    load_scene,
    rasterize,
    total_people,
    validate,
)


def facility(cx=14.0, cy=5.0, length=2.0, width=2.0, rotation=0.0,
             movable=True, resizable=False, region=None, fid="F1"):
    item = {
        "id": fid, "name": f"展台{fid}", "kind": "exhibit", "effect": "block",
        "center": [cx, cy], "length": length, "width": width,
        "rotation_deg": rotation, "movable": movable, "resizable": resizable,
    }
    if region is not None:
        item["allowed_region"] = region
    if resizable:
        item["size_limits"] = {"length": [1.0, 6.0], "width": [1.0, 6.0]}
    return item


def exit_width_declaration(name="主出口宽度（m）", current=4.0, limits=None):
    return {
        "name": name,
        "type": "exit_width",
        "target_id": "E1",
        "unit": "m",
        "allowed_range": limits or [2.0, 8.0],
        "current_value": current,
        "scene_diff": [{"exit_id": "E1", "width": current}],
    }


def center_x_declaration(name="西展台中心 x（m）", current=14.0, limits=None):
    return {
        "name": name,
        "type": "facility_center_x",
        "target_id": "F1",
        "unit": "m",
        "allowed_range": limits or [12.0, 16.0],
        "current_value": current,
        "scene_diff": [{"facility_id": "F1", "center": [current, 5.0]}],
    }


def scene(facilities=None, walls=None, policy=None, exits=None, zones=None):
    return {
        "version": "0.2",
        "meta": {"name": "测试场景", "revision": 1, "parent_revision": None},
        "geometry": {
            "size": [20.0, 10.0], "resolution": 1.0, "walls": walls or [],
            "obstacles": [], "facilities": facilities or [],
            "layout_policy": policy or {"min_passage_width": 0.0, "exit_clearance": 0.0},
        },
        "exits": exits or [{"id": "E1", "name": "南门", "side": "S", "offset": 8.0, "width": 4.0}],
        "crowd": {"zones": zones or [{
            "id": "Z1", "name": "人群", "polygon": [[2, 2], [8, 2], [8, 8], [2, 8]],
            "count": 20, "profile": "standing",
        }]},
        "params": {"walk_speed": 1.2, "T": 60, "dt": 0.1, "jam_density": 4.0},
    }


def messages(problems):
    return "\n".join(p["msg"] for p in problems)


class BackwardCompatibilityTests(unittest.TestCase):
    def test_v01_example_still_valid_and_exports(self):
        sc = load_scene(ROOT / "scenes/gym_example.json")
        probs = validate(sc)
        self.assertFalse([p for p in probs if p["level"] == "error"], messages(probs))
        R = rasterize(sc)
        self.assertEqual(R["walkable"].shape, (80, 120))
        self.assertAlmostEqual(total_people(R["rho0"], R["res"]), 1900.0, places=6)
        self.assertEqual(len(R["mask_hash"]), 64)
        self.assertEqual(len(R["input_hash"]), 64)

    def test_v02_example_valid(self):
        sc = load_scene(ROOT / "scenes/gym_facilities_example.json")
        probs = validate(sc)
        self.assertFalse([p for p in probs if p["level"] == "error"], messages(probs))
        R = rasterize(sc)
        self.assertEqual(R["facility_cells_by_id"]["F1"], 32)
        self.assertEqual(R["facility_cells_by_id"]["F2"], 32)

    def test_complex_arena_example_valid(self):
        sc = load_scene(ROOT / "scenes/arena_complex_example.json")
        probs = validate(sc)
        self.assertFalse([p for p in probs if p["level"] == "error"], messages(probs))
        R = rasterize(sc)
        self.assertEqual(R["walkable"].shape, (96, 144))
        self.assertEqual(len(R["facility_cells_by_id"]), 4)
        self.assertAlmostEqual(total_people(R["rho0"], R["res"]), 2950.0, places=6)


class RotatedFacilityTests(unittest.TestCase):
    def test_rotation_changes_occupied_cells(self):
        sc0 = scene(facilities=[facility(cx=10, cy=5, length=4, width=2, rotation=0)])
        sc90 = scene(facilities=[facility(cx=10, cy=5, length=4, width=2, rotation=90)])
        m0 = rasterize(sc0)["facility_mask"]
        m90 = rasterize(sc90)["facility_mask"]
        self.assertEqual(int(m0.sum()), 8)
        self.assertEqual(int(m90.sum()), 8)
        self.assertTrue(m0[4, 8] and m0[5, 11])
        self.assertFalse(m0[3, 9])
        self.assertTrue(m90[3, 9] and m90[6, 10])
        self.assertFalse(m90[4, 8])
        self.assertNotEqual(rasterize(sc0)["mask_hash"], rasterize(sc90)["mask_hash"])

    def test_rotated_facility_inside_allowed_region_valid(self):
        region = [[10, 0], [18, 0], [18, 10], [10, 10]]
        sc = scene(facilities=[facility(cx=14, cy=5, length=4, width=2,
                                        rotation=45, region=region)])
        probs = validate(sc)
        self.assertFalse([p for p in probs if p["level"] == "error"], messages(probs))

    def test_rotated_rect_overlap(self):
        a = facility_polygon([10, 5], 4, 2, 45)
        b = facility_polygon([10.5, 5], 4, 2, 0)
        self.assertTrue(convex_polygons_overlap(a, b))
        far = facility_polygon([15, 5], 2, 2, 0)
        self.assertFalse(convex_polygons_overlap(a, far))


class ConstraintTests(unittest.TestCase):
    def test_move_outside_allowed_region_rejected(self):
        region = [[12, 2], [18, 2], [18, 8], [12, 8]]
        sc = scene(facilities=[facility(cx=14, cy=5, region=region)])
        report = evaluate_layout(sc, [{"facility_id": "F1", "center": [10.0, 5.0]}])
        self.assertFalse(report["feasible"])
        self.assertIn("超出允许移动区域", messages(report["problems"]))

    def test_allowed_region_with_concave_notch_rejected(self):
        # C 形允许区：矩形四角在内，但凹口切进设施
        outer = [[8, 0], [18, 0], [18, 10], [8, 10], [8, 7], [13, 7], [13, 3], [8, 3]]
        poly = facility_polygon([12, 5], 6, 4, 0)
        self.assertFalse(polygon_inside_polygon(poly, outer))

    def test_facility_covering_crowd_rejected(self):
        sc = scene(facilities=[facility(cx=5, cy=5)])
        probs = validate(sc)
        self.assertIn("压到初始人群区", messages(probs))

    def test_facility_blocking_exit_rejected(self):
        sc = scene(facilities=[facility(cx=10, cy=1, length=4, width=2)])
        probs = validate(sc)
        self.assertIn("覆盖了出口", messages(probs))

    def test_facility_too_close_to_exit_rejected(self):
        sc = scene(facilities=[facility(cx=10, cy=1.4)],
                   policy={"min_passage_width": 0.0, "exit_clearance": 0.5})
        probs = validate(sc)
        self.assertIn("距出口", messages(probs))

    def test_facility_overlap_fixed_and_each_other(self):
        sc = scene(facilities=[facility(cx=14, cy=5), facility(cx=14.5, cy=5, fid="F2")])
        probs = validate(sc)
        self.assertIn("重叠", messages(probs))

    def test_narrow_passage_rejected(self):
        # 竖墙留 2m 口，门内净空约 0.5m；要求 2m 通路时不可行
        sc = scene(
            facilities=[facility(cx=14, cy=1)],
            walls=[[10, 0, 10, 4], [10, 6, 10, 10]],
            exits=[{"id": "W1", "name": "西门", "side": "W", "offset": 4.0, "width": 2.0}],
            zones=[{"id": "Z1", "name": "东侧", "polygon": [[12, 2], [18, 2], [18, 8], [12, 8]],
                    "count": 20, "profile": "standing"}],
            policy={"min_passage_width": 2.0, "exit_clearance": 0.0},
        )
        probs = validate(sc)
        self.assertIn("通道宽度不足", messages(probs))

    def test_facility_cutting_passage_rejected(self):
        sc = scene(
            facilities=[facility(cx=10, cy=5)],
            walls=[[10, 0, 10, 4], [10, 6, 10, 10]],
            exits=[{"id": "W1", "name": "西门", "side": "W", "offset": 4.0, "width": 2.0}],
            zones=[{"id": "Z1", "name": "东侧", "polygon": [[12, 2], [18, 2], [18, 8], [12, 8]],
                    "count": 20, "profile": "standing"}],
        )
        probs = validate(sc)
        self.assertIn("无法到达任何出口", messages(probs))

    def test_mass_preserved_for_feasible_layout(self):
        sc = scene(facilities=[facility(cx=14, cy=5)])
        R = rasterize(sc)
        self.assertAlmostEqual(total_people(R["rho0"], R["res"]), 20.0, places=6)


class ApplyLayoutTests(unittest.TestCase):
    def test_revision_and_original_unchanged(self):
        sc = scene(facilities=[facility(cx=14, cy=5)])
        before = json.dumps(sc, sort_keys=True)
        new = apply_layout(sc, [{"facility_id": "F1", "center": [15.0, 5.0]}])
        self.assertEqual(new["meta"]["revision"], 2)
        self.assertEqual(new["meta"]["parent_revision"], 1)
        self.assertEqual(new["geometry"]["facilities"][0]["center"], [15.0, 5.0])
        self.assertEqual(json.dumps(sc, sort_keys=True), before)

    def test_rejects_fixed_facility_and_resize(self):
        sc = scene(facilities=[facility(cx=14, cy=5, movable=False)])
        with self.assertRaises(LayoutError):
            apply_layout(sc, [{"facility_id": "F1", "center": [15.0, 5.0]}])
        sc2 = scene(facilities=[facility(cx=14, cy=5, resizable=False)])
        with self.assertRaises(LayoutError):
            apply_layout(sc2, [{"facility_id": "F1", "length": 3.0}])

    def test_rejects_unknown_field_or_facility(self):
        sc = scene(facilities=[facility(cx=14, cy=5)])
        with self.assertRaises(LayoutError):
            apply_layout(sc, [{"facility_id": "F1", "scale": 2.0}])
        with self.assertRaises(LayoutError):
            apply_layout(sc, [{"facility_id": "missing", "center": [15.0, 5.0]}])

    def test_resizable_within_limits(self):
        sc = scene(facilities=[facility(cx=14, cy=5, resizable=True)])
        new = apply_layout(sc, [{"facility_id": "F1", "length": 3.0}])
        self.assertEqual(new["geometry"]["facilities"][0]["length"], 3.0)
        with self.assertRaises(LayoutError):
            apply_layout(sc, [{"facility_id": "F1", "length": 9.0}])


class VariableParameterTests(unittest.TestCase):
    def test_declarations_load_validate_and_export(self):
        sc = scene()
        declaration = exit_width_declaration()
        sc["variable_parameters"] = [declaration]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "scene.json"
            source.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
            loaded = load_scene(source)
            self.assertFalse([p for p in validate(loaded) if p["level"] == "error"], messages(validate(loaded)))
            out = export_npz(loaded, Path(tmp) / "scene.npz")
            with np.load(out, allow_pickle=False) as data:
                exported = json.loads(str(data["variable_parameters_json"]))
            self.assertEqual(exported, [declaration])

    def test_v01_exports_empty_declarations_and_rejects_them_on_load(self):
        sc = scene()
        sc["version"] = "0.1"
        with tempfile.TemporaryDirectory() as tmp:
            out = export_npz(sc, Path(tmp) / "v01.npz")
            with np.load(out, allow_pickle=False) as data:
                self.assertEqual(json.loads(str(data["variable_parameters_json"])), [])
            sc["variable_parameters"] = [exit_width_declaration()]
            source = Path(tmp) / "invalid.json"
            source.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "需要场景版本 0.2"):
                load_scene(source)

    def test_declarations_do_not_change_solver_input_hashes(self):
        plain = scene(facilities=[facility(cx=14, cy=5)])
        declared = copy.deepcopy(plain)
        declared["variable_parameters"] = [center_x_declaration()]
        a, b = rasterize(plain), rasterize(declared)
        for key in ("mask_hash", "walkable_hash", "input_hash", "layout_hash"):
            self.assertEqual(a[key], b[key])

    def test_scene_description_is_a_valid_alternative_to_diff(self):
        sc = scene()
        declaration = exit_width_declaration()
        declaration.pop("scene_diff")
        declaration["scene_description"] = "将南侧主出口宽度调整为 6 米"
        sc["variable_parameters"] = [declaration]
        self.assertFalse([p for p in validate(sc) if p["level"] == "error"], messages(validate(sc)))

    def test_malformed_declarations_are_reported(self):
        cases = []

        def missing_name(d):
            d.pop("name")

        def unsupported_type(d):
            d["type"] = "unknown_parameter"

        def non_string_type(d):
            d["type"] = ["exit_width"]

        def wrong_unit(d):
            d["unit"] = "deg"

        def reversed_range(d):
            d["allowed_range"] = [8.0, 2.0]

        def nonfinite_range(d):
            d["allowed_range"] = [2.0, float("nan")]

        def current_outside_range(d):
            d["current_value"] = 9.0

        def current_mismatch(d):
            d["current_value"] = 5.0

        def unknown_target(d):
            d["target_id"] = "missing"

        def missing_result(d):
            d.pop("scene_diff")

        def both_results(d):
            d["scene_description"] = "同时提供两种结果"

        def unknown_field(d):
            d["scale"] = 2.0

        def nonfinite_current(d):
            d["current_value"] = float("inf")

        for label, mutate, expected in [
            ("缺少字段", missing_name, "缺少字段"),
            ("不支持", unsupported_type, "不支持"),
            ("类型非字符串", non_string_type, "type 必须是"),
            ("单位", wrong_unit, "unit 应为"),
            ("范围顺序", reversed_range, "min<=max"),
            ("非有限范围", nonfinite_range, "min<=max"),
            ("超出范围", current_outside_range, "超出 allowed_range"),
            ("当前值不一致", current_mismatch, "与场景实际值"),
            ("未知对象", unknown_target, "未知对象"),
            ("缺少结果", missing_result, "必须且只能提供"),
            ("两种结果", both_results, "必须且只能提供"),
            ("未知字段", unknown_field, "未知字段"),
            ("非有限当前值", nonfinite_current, "必须是有限数"),
        ]:
            with self.subTest(label=label):
                sc = scene()
                declaration = exit_width_declaration()
                mutate(declaration)
                sc["variable_parameters"] = [declaration]
                self.assertIn(expected, messages(validate(sc)))

    def test_non_array_declarations_fail_cleanly(self):
        sc = scene()
        sc["variable_parameters"] = None
        self.assertIn("variable_parameters 必须是数组", messages(validate(sc)))
        with self.assertRaisesRegex(LayoutError, "variable_parameters 必须是数组"):
            apply_layout(sc, [{"exit_id": "E1", "width": 5.0}])

    def test_duplicate_names_and_bindings_are_reported(self):
        sc = scene(facilities=[facility(cx=14, cy=5)])
        sc["variable_parameters"] = [
            exit_width_declaration(name="重复名称"),
            center_x_declaration(name="重复名称"),
        ]
        self.assertIn("name 重复", messages(validate(sc)))

        sc["variable_parameters"] = [
            exit_width_declaration(name="出口宽度 A"),
            exit_width_declaration(name="出口宽度 B"),
        ]
        self.assertIn("声明重复", messages(validate(sc)))

    def test_facility_declaration_requires_permission(self):
        sc = scene(facilities=[facility(cx=14, cy=5, movable=False)])
        sc["variable_parameters"] = [center_x_declaration()]
        self.assertIn("不允许移动/旋转", messages(validate(sc)))

    def test_apply_layout_refreshes_values_and_diffs(self):
        sc = scene(facilities=[facility(cx=14, cy=5)])
        sc["variable_parameters"] = [exit_width_declaration(), center_x_declaration()]
        new = apply_layout(sc, [
            {"exit_id": "E1", "width": 6.0},
            {"facility_id": "F1", "center": [15.0, 5.0]},
        ])
        by_type = {d["type"]: d for d in new["variable_parameters"]}
        self.assertEqual(by_type["exit_width"]["current_value"], 6.0)
        self.assertEqual(by_type["exit_width"]["scene_diff"], [{"exit_id": "E1", "width": 6.0}])
        self.assertEqual(by_type["facility_center_x"]["current_value"], 15.0)
        self.assertEqual(by_type["facility_center_x"]["scene_diff"],
                         [{"facility_id": "F1", "center": [15.0, 5.0]}])
        self.assertEqual(sc["exits"][0]["width"], 4.0)
        self.assertEqual(sc["geometry"]["facilities"][0]["center"], [14.0, 5.0])

    def test_candidate_outside_declared_range_is_rejected(self):
        sc = scene()
        sc["variable_parameters"] = [exit_width_declaration()]
        with self.assertRaisesRegex(LayoutError, "超出 allowed_range"):
            apply_layout(sc, [{"exit_id": "E1", "width": 9.0}])
        report = evaluate_layout(sc, [{"exit_id": "E1", "width": 9.0}])
        self.assertFalse(report["feasible"])
        self.assertIn("超出 allowed_range", messages(report["problems"]))

    def test_exit_candidate_reports_width_and_parameter_deltas(self):
        sc = scene()
        sc["variable_parameters"] = [exit_width_declaration()]
        report = evaluate_layout(sc, [{"exit_id": "E1", "width": 5.0}], candidate_id="W1")
        self.assertTrue(report["feasible"])
        self.assertTrue(report["solve_required"])
        self.assertEqual(report["changed_facilities"], [])
        self.assertEqual(report["changed_exits"], ["E1"])
        self.assertEqual(report["exit_width_deltas"], [{
            "exit_id": "E1", "before_width_m": 4.0,
            "after_width_m": 5.0, "width_delta_m": 1.0,
        }])
        self.assertFalse(report["mask_changed"])
        self.assertEqual(report["variable_parameters"][0]["current_value"], 5.0)
        delta = report["variable_parameter_deltas"][0]
        self.assertEqual((delta["before_value"], delta["after_value"], delta["delta"]), (4.0, 5.0, 1.0))
        self.assertTrue(delta["changed"])
        self.assertEqual(delta["scene_diff"], [{"exit_id": "E1", "width": 5.0}])
        self.assertNotEqual(report["input_hash"], rasterize(sc)["input_hash"])

    def test_exit_candidates_are_deduplicated_by_input_hash(self):
        sc = scene()
        sc["variable_parameters"] = [exit_width_declaration()]
        report = evaluate_candidates(sc, [
            {"candidate_id": "W1", "changes": [{"exit_id": "E1", "width": 5.0}]},
            {"candidate_id": "W2", "changes": [{"exit_id": "E1", "width": 5.0}]},
            {"candidate_id": "W3", "changes": [{"exit_id": "E1", "width": 6.0}]},
        ])
        by_id = {item["candidate_id"]: item for item in report["candidates"]}
        self.assertTrue(by_id["W1"]["solve_required"])
        self.assertEqual(by_id["W2"]["duplicate_of"], "W1")
        self.assertFalse(by_id["W2"]["solve_required"])
        self.assertTrue(by_id["W3"]["solve_required"])
        self.assertEqual(report["unique_solve_count"], 2)
        self.assertEqual(report["variable_parameters"][0]["current_value"], 4.0)


class DedupeTests(unittest.TestCase):
    def test_subgrid_moves_have_same_mask_and_are_skipped(self):
        sc = scene(facilities=[facility(cx=14, cy=5, length=4)])
        report = evaluate_candidates(sc, [
            {"candidate_id": "C1", "changes": [{"facility_id": "F1", "center": [14.6, 5.0]}]},
            {"candidate_id": "C2", "changes": [{"facility_id": "F1", "center": [14.7, 5.0]}]},
            {"candidate_id": "C3", "changes": [{"facility_id": "F1", "center": [15.5, 5.0]}]},
        ])
        by_id = {r["candidate_id"]: r for r in report["candidates"]}
        self.assertTrue(by_id["C1"]["solve_required"])
        self.assertEqual(by_id["C2"]["duplicate_of"], "C1")
        self.assertFalse(by_id["C2"]["solve_required"])
        self.assertNotEqual(by_id["C1"]["mask_hash"], by_id["C3"]["mask_hash"])
        self.assertEqual(report["unique_solve_count"], 2)

    def test_noop_candidate_is_baseline_duplicate(self):
        sc = scene(facilities=[facility(cx=14, cy=5)])
        report = evaluate_candidates(sc, [
            {"candidate_id": "C1", "changes": [{"facility_id": "F1", "center": [14.0, 5.0]}]},
        ])
        self.assertEqual(report["candidates"][0]["duplicate_of"], "baseline")
        self.assertFalse(report["candidates"][0]["solve_required"])
        self.assertEqual(report["unique_solve_count"], 0)


class ExportTests(unittest.TestCase):
    def test_npz_contains_masks_and_hashes(self):
        sc = scene(facilities=[facility(cx=14, cy=5)])
        with tempfile.TemporaryDirectory() as tmp:
            out = export_npz(sc, Path(tmp) / "scene.npz")
            with np.load(out, allow_pickle=False) as d:
                for key in ("rho0", "walkable", "obstacle_mask", "facility_mask", "zone_id",
                            "mask_hash", "walkable_hash", "input_hash", "layout_hash",
                            "scene_version", "scene_revision"):
                    self.assertIn(key, d.files)
                self.assertEqual(len(str(d["mask_hash"])), 64)
                self.assertTrue(bool(d["obstacle_mask"][5, 14]))
                self.assertFalse(bool(d["walkable"][5, 14]))

    def test_export_rejects_infeasible_layout(self):
        sc = scene(facilities=[facility(cx=5, cy=5)])   # 压初始人群
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                export_npz(sc, Path(tmp) / "bad.npz")


if __name__ == "__main__":
    unittest.main()
