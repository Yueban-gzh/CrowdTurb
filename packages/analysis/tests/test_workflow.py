"""Sampling, input identity, fairness checks and decisions on explicitly synthetic runs."""

import copy
import json
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages/analysis"))
from crowdturb_analysis.core import analyze, initial_state_hash
from crowdturb_analysis.density import aggregate, estimator_config
from crowdturb_analysis.sampling import auto_candidates
from crowdturb_analysis.comparison import compare_candidates, expected_input_errors
from crowdturb_analysis.demo import build_demo, mock_trajectory
from scene_io import load_scene, rasterize
from layout_eval import apply_layout, evaluate_candidates


def fixture_scene():
    scene = load_scene(ROOT / "apps/web/scene-config/scenes/passage_optimization_example.json")
    scene["crowd"]["zones"][0]["count"] = 2
    return scene


trajectory = mock_trajectory

def policy():
    return {"limits": {"exit_expansion_m": 2, "passage_added_area_m2": 8, "facility_move_m": 14,
                       "facility_rotation_deg": 0}, "remaining_tolerance": 0, "congestion_tolerance": 1e-9}


class DensityTests(unittest.TestCase):
    def setUp(self):
        self.grid = {"nx": 6, "ny": 4, "dx": 1.0, "dy": 1.0, "origin": [0, 0]}
        self.mask = np.ones((4, 6), dtype=bool)

    def test_gaussian_conserves_mass_and_vector_mean(self):
        agents = [{"position_m": [2.9, 1.5], "velocity_mps": [2, 0]},
                  {"position_m": [2.9, 1.5], "velocity_mps": [-2, 0]}]
        density, velocity = aggregate(agents, self.grid, self.mask, estimator_config("gaussian_visible", 0.7))
        self.assertAlmostEqual(density.sum(), 2)
        np.testing.assert_allclose(velocity, 0)

    def test_small_translation_no_longer_causes_cell_density_jump(self):
        cell, smooth = [], []
        for offset in (0, 0.02):
            agents = [{"position_m": [x + offset, 1.5], "velocity_mps": [0, 0]} for x in (2.97, 2.99)]
            cell.append(aggregate(agents, self.grid, self.mask, estimator_config())[0])
            smooth.append(aggregate(agents, self.grid, self.mask, estimator_config("gaussian_visible", 0.7))[0])
        self.assertGreater(np.abs(cell[1] - cell[0]).sum(), 1)
        self.assertLess(np.abs(smooth[1] - smooth[0]).sum(), 0.15)

    def test_smoothing_does_not_cross_wall_and_preserves_boundary_mass(self):
        self.mask[:, 3] = False
        agents = [{"position_m": [2.9, 1.5], "velocity_mps": [1, 0]}]
        density, _ = aggregate(agents, self.grid, self.mask, estimator_config("gaussian_visible", 1))
        self.assertEqual(density[:, 3:].sum(), 0)
        self.assertAlmostEqual(density.sum(), 1)

    def test_density_integral_with_nonunit_cells_and_empty_frame(self):
        self.grid.update(dx=0.5, dy=0.5)
        agents = [{"position_m": [0.1, 0.1], "velocity_mps": [1, 0]}]
        config = estimator_config("gaussian_visible", 0.5)
        density, _ = aggregate(agents, self.grid, self.mask, config)
        self.assertAlmostEqual(density.sum() * 0.25, 1)
        density, velocity = aggregate([], self.grid, self.mask, config)
        self.assertEqual(density.sum(), 0)
        self.assertEqual(velocity.sum(), 0)

    def test_unresolved_bandwidth_is_rejected(self):
        with self.assertRaises(ValueError):
            aggregate([{"position_m": [0.01, 0.01], "velocity_mps": [0, 0]}], self.grid, self.mask,
                      estimator_config("gaussian_visible", 0.001))

    def test_corner_crossing_does_not_cut_through_diagonal_obstacles(self):
        self.mask[0, 1] = False
        self.mask[1, 0] = False
        density, _ = aggregate([{"position_m": [0.5, 0.5], "velocity_mps": [0, 0]}],
                               self.grid, self.mask, estimator_config("gaussian_visible", 2))
        self.assertEqual(density[1, 1], 0)
        self.assertAlmostEqual(density.sum(), 1)


class SamplingTests(unittest.TestCase):
    def test_range_sampling_keeps_current_and_endpoints_within_budget(self):
        scene = fixture_scene()
        report = auto_candidates(scene, points_per_parameter=7, max_candidates=27)
        self.assertLessEqual(report["candidate_count"], 27)
        for sample, declaration in zip(report["search"]["samples"], scene["variable_parameters"]):
            self.assertTrue(set(declaration["allowed_range"] + [declaration["current_value"]]) <= set(sample["values"]))
        self.assertTrue(any(c["duplicate_of"] == "baseline" for c in report["candidates"]))

    def test_impossible_budget_does_not_drop_baseline_or_range_endpoints(self):
        with self.assertRaisesRegex(ValueError, "at least 27"):
            auto_candidates(fixture_scene(), max_candidates=26)

    def test_selecting_single_parameter_and_fixed_range(self):
        scene = fixture_scene()
        scene["variable_parameters"][1].update(allowed_range=[4, 4])
        report = auto_candidates(scene, [{"type": "passage_width", "target_id": "P1"}], max_candidates=1)
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["candidates"][0]["duplicate_of"], "baseline")


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.scene = fixture_scene()
        self.changes = [{"passage_id": "P1", "width": 6}]
        self.candidate_scene = apply_layout(self.scene, self.changes)
        self.report = evaluate_candidates(self.scene, [{"candidate_id": "C1", "changes": self.changes}])
        self.baseline = trajectory(self.scene, "mock-base")
        self.candidate = trajectory(self.candidate_scene, "mock-candidate", "improved")

    def compare(self, runs=None, selected_policy=None, report=None):
        return compare_candidates(self.scene, report or self.report, self.baseline,
                                  {"C1": self.candidate} if runs is None else runs,
                                  selected_policy or policy(), 1.5, "synthetic threshold only")

    def test_improvement_has_units_cost_delta_and_recommendation(self):
        result = self.compare()
        row = result["candidates"][0]
        self.assertEqual(result["recommendation"], "C1")
        self.assertEqual(row["status"], "improved")
        self.assertEqual(row["deltas"]["remaining_at_end"], -1)
        self.assertEqual(row["costs"]["passage_added_area_m2"], 8)
        self.assertEqual(row["rank"], 1)

    def test_changed_initial_state_seed_model_behavior_times_are_incomparable(self):
        for field in ("initial", "seed", "model", "behavior", "times"):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.candidate)
                if field == "initial":
                    candidate["frames"][0]["agents"][0]["position_m"][0] += 0.01
                    candidate["initial_state_hash"] = initial_state_hash(candidate["frames"][0]["agents"])
                elif field == "seed":
                    candidate["simulation_settings"]["seed"] = 3
                elif field == "model":
                    candidate["model_sha256"] = "a" * 64
                elif field == "behavior":
                    candidate.pop("behavior_hash")
                else:
                    candidate["frames"][-1]["time_s"] = 3
                result = self.compare({"C1": candidate})
                self.assertEqual(result["candidates"][0]["status"], "incomparable")
                self.assertEqual(result["recommendation"], "baseline")

    def test_copied_hash_cannot_hide_wrong_mask(self):
        self.candidate["walkable"][0][0] = True
        row = self.compare()["candidates"][0]
        self.assertEqual(row["status"], "incomparable")
        self.assertTrue(any("mask" in reason for reason in row["reasons"]))

    def test_corrupt_initial_count_or_support_rejected(self):
        candidate = copy.deepcopy(self.candidate)
        for frame in candidate["frames"]:
            frame["agents"] = []
        candidate["exit_events"] = []
        candidate["initial_state_hash"] = initial_state_hash([])
        row = self.compare({"C1": candidate})["candidates"][0]
        self.assertEqual(row["status"], "incomparable")
        self.assertTrue(any("count" in reason for reason in row["reasons"]))

    def test_budget_failure_missing_duplicate_invalid_and_failure_stay_explicit(self):
        p = policy()
        p["limits"]["passage_added_area_m2"] = 4
        self.assertEqual(self.compare(selected_policy=p)["candidates"][0]["status"], "over_budget")
        self.assertEqual(self.compare({})["candidates"][0]["status"], "pending")
        result = self.compare({"C1": {"status": "failed", "reason": "planner failed"}})
        self.assertEqual(result["candidates"][0]["reasons"], ["planner failed"])
        report = evaluate_candidates(self.scene, [
            {"candidate_id": "same", "changes": [{"passage_id": "P1", "width": 4}]},
            {"candidate_id": "bad", "changes": [{"passage_id": "P1", "width": 100}]},
        ])
        result = self.compare({}, report=report)
        self.assertEqual([r["status"] for r in result["candidates"]], ["duplicate", "invalid"])

    def test_tradeoff_worsening_and_unchanged_do_not_force_recommendation(self):
        for outcome, status in (("tradeoff", "tradeoff"), ("worsened", "worsened"), ("baseline", "unchanged")):
            with self.subTest(outcome=outcome):
                candidate = trajectory(self.candidate_scene, "mock-" + outcome, outcome)
                result = self.compare({"C1": candidate})
                self.assertEqual(result["candidates"][0]["status"], status)
                self.assertEqual(result["recommendation"], "baseline")

    def test_zero_baseline_does_not_divide_by_zero(self):
        self.baseline = trajectory(self.scene, "mock-base", "improved")
        row = self.compare()["candidates"][0]
        self.assertIsNone(row["improvement_percent"]["remaining_at_end"])

    def test_reconstructs_deltas_instead_of_trusting_report(self):
        self.report["candidates"][0]["passage_width_deltas"][0]["clear_area_delta_m2"] = 0
        result = self.compare()
        self.assertEqual(result["candidates"][0]["costs"]["passage_added_area_m2"], 8)

    def test_unknown_runs_and_reused_result_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.compare({"unknown": self.candidate})
        self.candidate["result_id"] = self.baseline["result_id"]
        self.assertEqual(self.compare()["candidates"][0]["status"], "incomparable")

    def test_wrong_hash_revision_and_population_support_prevent_comparison(self):
        for key in ("input_hash", "scene_revision", "position"):
            candidate = copy.deepcopy(self.candidate)
            if key == "input_hash":
                candidate[key] = "0" * 64
            elif key == "scene_revision":
                candidate[key] = 999
            else:
                candidate["frames"][0]["agents"][0]["position_m"] = [1.0, 1.0]
                candidate["initial_state_hash"] = initial_state_hash(candidate["frames"][0]["agents"])
            with self.subTest(key=key):
                self.assertEqual(self.compare({"C1": candidate})["candidates"][0]["status"], "incomparable")

    def test_invalid_trace_yields_failure_not_successful_zero_metrics(self):
        self.candidate["exit_events"] = []
        row = self.compare()["candidates"][0]
        self.assertEqual(row["status"], "failed")
        self.assertIsNone(row["metrics"])

    def test_gaussian_comparison_records_scale_and_never_mixes_estimators(self):
        result = compare_candidates(self.scene, self.report, self.baseline, {"C1": self.candidate},
                                    policy(), 0.1, "experimental threshold", density_method="gaussian_visible", bandwidth_m=1)
        self.assertEqual(result["density_estimator"]["bandwidth_m"], 1)
        self.assertEqual(result["density_estimator"]["method"], "gaussian_visible")

    def test_complete_mock_demo_retains_multiple_outcomes_and_source(self):
        payload = build_demo(self.scene)
        comparison = payload["comparison"]
        statuses = {r["status"] for r in comparison["candidates"]}
        self.assertTrue({"improved", "unchanged", "worsened", "tradeoff", "pending", "failed", "incomparable", "duplicate", "over_budget"} <= statuses)
        self.assertEqual(comparison["source"], "mock")
        self.assertTrue(all(t.get("source") == "mock" for t in payload["runs"].values() if t.get("status") != "failed"))


if __name__ == "__main__":
    unittest.main()
