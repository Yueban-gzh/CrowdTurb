"""Regression checks against plausible integration mistakes, not solver tests."""

import copy
import sys
import unittest
from pathlib import Path

from jsonschema import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from check_repository import ROOT, load_json, validate_result, validate_scene, validate_task


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.scene = load_json(ROOT / "examples/scene.mock.json")
        self.result = load_json(ROOT / "examples/field.mock.json")
        self.task = load_json(ROOT / "examples/task.mock.json")

    def rejects(self, callback):
        with self.assertRaises((ValueError, ValidationError)):
            callback()

    def test_shared_mock_bundle_is_valid(self):
        validate_scene(self.scene)
        validate_result(self.result, self.scene)
        validate_task(self.task, self.scene, self.result)

    def test_negative_or_nonfinite_density_rejected(self):
        for value in (-0.1, float("nan"), float("inf")):
            with self.subTest(value=value):
                bad = copy.deepcopy(self.result)
                bad["frames"][0]["density"][0][0] = value
                self.rejects(lambda: validate_result(bad, self.scene))

    def test_transposed_grid_rejected(self):
        self.scene["initial_density"] = [list(row) for row in zip(*self.scene["initial_density"])]
        self.rejects(lambda: validate_scene(self.scene))

    def test_blocked_density_and_velocity_rejected(self):
        self.scene["initial_density"][1][1] = 1
        self.rejects(lambda: validate_scene(self.scene))
        self.result["frames"][0]["velocity"][1][1] = [1, 0]
        self.rejects(lambda: validate_result(self.result, self.scene))

    def test_unknown_exit_target_and_invalid_range_rejected(self):
        bad = copy.deepcopy(self.scene)
        bad["adjustable_parameters"][0]["target_id"] = "missing"
        self.rejects(lambda: validate_scene(bad))
        self.scene["adjustable_parameters"][0]["min_m"] = 4
        self.rejects(lambda: validate_scene(self.scene))

    def test_interior_or_oversized_exit_rejected(self):
        bad = copy.deepcopy(self.scene)
        bad["exits"][0]["cells"] = [[2, 1]]
        self.rejects(lambda: validate_scene(bad))
        self.scene["exits"][0]["width_m"] = 4
        self.rejects(lambda: validate_scene(self.scene))

    def test_mismatched_scene_revision_rejected(self):
        self.result["scene_revision"] = 2
        self.rejects(lambda: validate_result(self.result, self.scene))

    def test_unordered_or_duplicate_timestamps_rejected(self):
        for time in (0.0, -1.0):
            with self.subTest(time=time):
                self.result["frames"][1]["time_s"] = time
                self.rejects(lambda: validate_result(self.result, self.scene))

    def test_nonmock_result_requires_experiment_record(self):
        self.result["source"] = "pde"
        self.rejects(lambda: validate_result(self.result, self.scene))

    def test_completed_task_cannot_reference_another_result_or_source(self):
        bad = copy.deepcopy(self.task)
        bad["result_id"] = "another-result"
        self.rejects(lambda: validate_task(bad, self.scene, self.result))
        self.task["source"] = "pde"
        self.rejects(lambda: validate_task(self.task, self.scene, self.result))

    def test_task_snapshots_require_matching_payload(self):
        self.task["status"] = "running"
        self.rejects(lambda: validate_task(self.task, self.scene))
        self.task["status"] = "failed"
        self.task["result_id"] = None
        self.rejects(lambda: validate_task(self.task, self.scene))
        self.task["error"] = {"code": "mock_failure", "message": "Synthetic failure"}
        validate_task(self.task, self.scene)


if __name__ == "__main__":
    unittest.main()
