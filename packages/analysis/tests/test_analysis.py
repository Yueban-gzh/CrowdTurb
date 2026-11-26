"""Hand-calculated invariants and rejection of incomplete/corrupt trajectories."""

import copy
import json
from pathlib import Path
import sys
import unittest

from jsonschema import ValidationError
import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "packages/analysis"))
from crowdturb_analysis.core import analyze, initial_state_hash, validate_trajectory, check_schema
from crowdturb_analysis.candidates import generate_candidates
from scene_io import load_scene


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((REPO / "examples/trajectory.mock.json").read_text(encoding="utf-8"))

    def reject(self, data):
        with self.assertRaises((ValueError, ValidationError)):
            analyze(data, 1.5, "hand calculation")

    def test_hand_computed_counts_density_velocity_and_nonuniform_time_integral(self):
        field, result = analyze(self.data, 1.5, "hand calculation")
        self.assertEqual(result["metrics"]["remaining_at_end"]["value"], 1)
        self.assertEqual(result["metrics"]["exited_people"]["value"], 2)
        self.assertEqual(result["metrics"]["congestion_area_time"]["value"], 1.5)
        self.assertEqual(field["frames"][0]["density"][0][0], 2)
        self.assertEqual(field["frames"][0]["velocity"][0][0], [2, 0])
        self.assertEqual(field["frames"][0]["velocity"][1][2], [0, 0])
        self.assertEqual(result["series"][0]["values"], [3, 2, 1])
        self.assertEqual(result["series"][2]["values"], [0, 1, 2])
        self.assertEqual(result["hotspots"][0]["observed_duration_s"], 1.5)
        self.assertEqual(result["hotspots"][0]["end_time_s"], 1.5)

    def test_person_conservation_between_counts_and_grid_integrals(self):
        field, result = analyze(self.data, 1.5, "hand calculation")
        for frame, expected in zip(field["frames"], [3, 2, 1]):
            self.assertAlmostEqual(float(np.array(frame["density"]).sum()), expected)
        check_schema(field, "derived-field")

    def test_nonunit_cell_area_scales_density_and_congested_area(self):
        self.data["grid"]["dx"] = 2.0
        for frame in self.data["frames"]:
            for agent in frame["agents"]:
                agent["position_m"][0] *= 2
        self.data["initial_state_hash"] = initial_state_hash(self.data["frames"][0]["agents"])
        field, result = analyze(self.data, 0.75, "nonunit cell hand calculation")
        self.assertEqual(field["frames"][0]["density"][0][0], 1)
        self.assertEqual(float(np.array(field["frames"][0]["density"]).sum()) * 2, 3)
        self.assertEqual(result["series"][1]["values"], [2, 0, 0])
        self.assertEqual(result["metrics"]["congestion_area_time"]["value"], 3)

    def test_threshold_is_strict_and_no_last_frame_extrapolation(self):
        _, result = analyze(self.data, 2, "strict threshold")
        self.assertEqual(result["metrics"]["congestion_area_time"]["value"], 0)
        self.assertEqual(result["hotspots"], [])
        self.data["frames"] = self.data["frames"][:1]
        self.data["exit_events"] = []
        _, result = analyze(self.data, 1.5, "single snapshot")
        self.assertEqual(result["metrics"]["congestion_area_time"]["value"], 0)
        self.assertEqual(result["hotspots"][0]["observed_duration_s"], 0)

    def test_reordered_people_do_not_change_aggregation_or_initial_hash(self):
        expected = analyze(self.data, 1.5, "same")
        for frame in self.data["frames"]:
            frame["agents"].reverse()
        self.assertEqual(initial_state_hash(self.data["frames"][0]["agents"]), self.data["initial_state_hash"])
        self.assertEqual(analyze(self.data, 1.5, "same"), expected)

    def test_missing_person_is_not_counted_as_evacuation(self):
        self.data["exit_events"] = []
        self.reject(self.data)

    def test_outside_person_and_obstacle_person_are_errors(self):
        for position in ([3, 0.5], [-0.1, 0.5], [1.5, 1.5]):
            with self.subTest(position=position):
                bad = copy.deepcopy(self.data)
                bad["frames"][1]["agents"][0]["position_m"] = position
                self.reject(bad)

    def test_duplicate_or_unknown_exit_events_rejected(self):
        bad = copy.deepcopy(self.data)
        bad["exit_events"].append(copy.deepcopy(bad["exit_events"][0]))
        self.reject(bad)
        self.data["exit_events"][0]["exit_id"] = "missing"
        self.reject(self.data)

    def test_duplicate_ids_and_unannounced_arrivals_rejected(self):
        for frame in (0, 1):
            bad = copy.deepcopy(self.data)
            bad["frames"][frame]["agents"].append(copy.deepcopy(bad["frames"][frame]["agents"][0]))
            self.reject(bad)
        self.data["frames"][1]["agents"][0]["agent_id"] = "new"
        self.reject(self.data)

    def test_unsorted_time_nonfinite_and_mask_shape_rejected(self):
        bad = copy.deepcopy(self.data)
        bad["frames"][1]["time_s"] = 0
        self.reject(bad)
        bad = copy.deepcopy(self.data)
        bad["frames"][1]["agents"][0]["velocity_mps"][0] = float("nan")
        self.reject(bad)
        self.data["walkable"].pop()
        self.reject(self.data)

    def test_initial_state_hash_detects_changed_start(self):
        self.data["frames"][0]["agents"][0]["position_m"][0] += 0.01
        self.reject(self.data)

    def test_stddn_cannot_be_labeled_without_model_and_run_provenance(self):
        self.data["source"] = "stddn"
        self.reject(self.data)

    def test_exit_exactly_on_frame_is_removed_from_that_frame(self):
        self.data["exit_events"][0]["time_s"] = 1.5
        validate_trajectory(self.data)
        self.data["exit_events"][0]["time_s"] = 1.6
        self.reject(self.data)

    def test_empty_population_supported(self):
        for frame in self.data["frames"]:
            frame["agents"] = []
        self.data["exit_events"] = []
        self.data["initial_state_hash"] = initial_state_hash([])
        _, result = analyze(self.data, 1.5, "empty")
        self.assertEqual(result["metrics"]["remaining_at_end"]["value"], 0)
        self.assertEqual(result["hotspots"], [])


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.scene = load_scene(REPO / "apps/web/scene-config/scenes/passage_optimization_example.json")
        self.samples = json.loads((REPO / "examples/layout-samples.mock.json").read_text(encoding="utf-8"))

    def test_three_parameters_generate_real_changes_without_mutating_baseline(self):
        original = copy.deepcopy(self.scene)
        report = generate_candidates(self.scene, self.samples, 18)
        self.assertEqual(self.scene, original)
        self.assertEqual(report["candidate_count"], 18)
        self.assertEqual(report["search"]["evaluation_status"], "not_evaluated")
        self.assertTrue(any(c["changed_passages"] for c in report["candidates"]))
        self.assertTrue(any(c["solve_required"] for c in report["candidates"]))

    def test_budget_and_undeclared_or_outside_samples_rejected(self):
        with self.assertRaises(ValueError):
            generate_candidates(self.scene, self.samples, 17)
        self.samples[0]["values"] = [100]
        with self.assertRaises(ValueError):
            generate_candidates(self.scene, self.samples)
        self.samples[0]["type"] = "exit_position"
        with self.assertRaises(ValueError):
            generate_candidates(self.scene, self.samples)

    def test_noop_is_preserved_as_baseline_duplicate(self):
        report = generate_candidates(self.scene, [{"type": "passage_width", "target_id": "P1", "values": [4]}])
        self.assertEqual(report["candidates"][0]["duplicate_of"], "baseline")
        self.assertFalse(report["candidates"][0]["solve_required"])


if __name__ == "__main__":
    unittest.main()
