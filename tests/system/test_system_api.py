"""End-to-end checks for the E-owned W1 system foundation."""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.crowdturb.repository import load_json
from apps.api.crowdturb.server import ApiServer
from apps.api.crowdturb.service import AppService


class ApiServerHarness:
    def __init__(
        self, database_path: Path, team_fixtures: Path | None = None, model_path: Path | None = None,
    ) -> None:
        self.server = ApiServer(
            host="127.0.0.1",
            port=0,
            database_path=database_path,
            seed_demo=False,
            team_fixtures=team_fixtures,
            model_path=model_path,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return self.server.url

    def close(self) -> None:
        self.server.httpd.shutdown()
        self.thread.join(timeout=5)
        self.server.close()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))


class SystemApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "crowdturb.sqlite3"
        self.api = ApiServerHarness(self.db_path)
        self.scene = load_json(ROOT / "examples" / "scene.mock.json")

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def create_project(self, name: str = "API test") -> dict:
        status, payload = self.api.request("POST", "/api/projects", {"name": name})
        self.assertEqual(status, 201, payload)
        return payload

    def wait_for_task(self, task_id: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            status, payload = self.api.request("GET", f"/api/tasks/{task_id}")
            self.assertEqual(status, 200, payload)
            last = payload["task"]
            if last["status"] in ("succeeded", "failed"):
                return last
            time.sleep(0.04)
        self.fail(f"task did not finish: {last}")

    def test_health_and_static_shell_are_available(self) -> None:
        status, health = self.api.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["adapters"]["mock"], "ready")
        self.assertEqual(health["adapters"]["pde"], "not_configured")

        with urlopen(f"{self.api.url}/", timeout=5) as response:
            html = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("CrowdTurb", html)
        self.assertIn("W1 Mock 联调", html)

    def test_project_creation_and_scene_revision_round_trip(self) -> None:
        created = self.create_project()
        project_id = created["project"]["project_id"]
        self.assertEqual(created["scene"]["revision"], 1)
        self.assertEqual(created["project"]["current_revision"], 1)

        changed = copy.deepcopy(created["scene"])
        changed["description"] = "Edited through the API for revision round-trip."
        status, saved = self.api.request(
            "POST", f"/api/projects/{project_id}/scenes", {"scene": changed}
        )
        self.assertEqual(status, 201, saved)
        self.assertEqual(saved["scene"]["revision"], 2)

        status, latest = self.api.request("GET", f"/api/projects/{project_id}/scenes/latest")
        self.assertEqual(status, 200)
        self.assertEqual(latest["scene"]["revision"], 2)
        self.assertEqual(latest["scene"]["description"], changed["description"])

        status, detail = self.api.request("GET", f"/api/projects/{project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["project"]["current_revision"], 2)

    def test_invalid_scene_is_rejected_without_saving(self) -> None:
        created = self.create_project()
        project_id = created["project"]["project_id"]
        invalid = copy.deepcopy(created["scene"])
        invalid["initial_density"] = invalid["initial_density"][:-1]
        status, payload = self.api.request(
            "POST", f"/api/projects/{project_id}/scenes", {"scene": invalid}
        )
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["error"]["code"], "invalid_scene")

        status, detail = self.api.request("GET", f"/api/projects/{project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["project"]["current_revision"], 1)

    def test_mock_task_succeeds_and_persists_result(self) -> None:
        created = self.create_project()
        project_id = created["project"]["project_id"]
        status, submitted = self.api.request(
            "POST",
            f"/api/projects/{project_id}/tasks",
            {"source": "mock", "scene_revision": 1},
        )
        self.assertEqual(status, 202, submitted)
        task = self.wait_for_task(submitted["task"]["task_id"])
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["source"], "mock")
        self.assertIsNotNone(task["result_id"])

        status, payload = self.api.request("GET", f"/api/results/{task['result_id']}")
        self.assertEqual(status, 200)
        result = payload["result"]
        self.assertEqual(result["source"], "mock")
        self.assertEqual(result["scene_revision"], 1)
        self.assertEqual(len(result["frames"]), 2)

        status, detail = self.api.request("GET", f"/api/projects/{project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["tasks"][0]["status"], "succeeded")
        self.assertEqual(detail["results"][0]["result_id"], task["result_id"])

    def test_unconfigured_pde_task_fails_with_readable_reason(self) -> None:
        created = self.create_project()
        project_id = created["project"]["project_id"]
        status, submitted = self.api.request(
            "POST", f"/api/projects/{project_id}/tasks", {"source": "pde"}
        )
        self.assertEqual(status, 202, submitted)
        task = self.wait_for_task(submitted["task"]["task_id"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "solver_not_configured")
        self.assertIn("C 提供独立运行入口", task["error"]["message"])
        self.assertIsNone(task["result_id"])

    def test_unknown_project_and_source_are_reported(self) -> None:
        status, payload = self.api.request("GET", "/api/projects/prj-missing")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "project_not_found")

        created = self.create_project()
        project_id = created["project"]["project_id"]
        status, payload = self.api.request(
            "POST", f"/api/projects/{project_id}/tasks", {"source": "unknown"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "unsupported_source")


    def test_review_import_without_fixtures_reports_configuration_error(self) -> None:
        status, payload = self.api.request("POST", "/api/review-runs", {"kind": "playback"})
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "review_samples_not_configured")
        self.assertEqual(self.api.request("GET", "/api/review-runs"), (200, {"runs": []}))


@unittest.skipUnless(os.environ.get("CROWDTURB_TEAM_FIXTURES"), "requires a D branch snapshot")
class TeamReviewTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("CROWDTURB_ONNX_MODEL"), "requires the team's local ONNX file")
    def test_stddn_reaches_b_and_d_with_a_real_candidate(self) -> None:
        fixture_root = Path(os.environ["CROWDTURB_TEAM_FIXTURES"])
        model_path = Path(os.environ["CROWDTURB_ONNX_MODEL"])
        scene_path = Path(__file__).resolve().parents[2] / "examples/workbench.route-demo.json"
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
        binding = {"type": "facility_center_x", "target_id": "F1"}
        with tempfile.TemporaryDirectory() as temp:
            api = ApiServerHarness(Path(temp) / "stddn.sqlite3", fixture_root, model_path)
            try:
                status, payload = api.request("POST", "/api/workbench/runs", {
                    "scene": scene, "parameter": binding,
                })
                self.assertEqual(status, 202, payload)
                self.assertEqual(payload["run"]["source"], "stddn")
                run = self.wait_for_preview(api, payload["run"]["run_id"])["run"]
                self.assertEqual(run["status"], "succeeded", run)
                self.assertEqual(run["parameter"], binding)
                self.assertEqual(len(run["views"]), 2)
                self.assertEqual(len(run["model_sha256"]), 64)
                artifacts = []
                for view in run["views"]:
                    prefix = f"/api/workbench/runs/{run['run_id']}/views/{view}"
                    manifest = api.request("GET", prefix)[1]
                    self.assertEqual(manifest["source"], "stddn")
                    bundle = {name: api.request("GET", url)[1] for name, url in manifest["artifacts"].items()}
                    trajectory = bundle["trajectory"]
                    self.assertEqual(len(trajectory["exit_events"]), 44)
                    self.assertFalse(trajectory["frames"][-1]["agents"])
                    self.assertEqual({event["exit_id"] for event in trajectory["exit_events"]}, {"E1"})
                    self.assertEqual(bundle["analysis"]["input_hash"], trajectory["input_hash"])
                    self.assertEqual(bundle["field"]["input_hash"], trajectory["input_hash"])
                    self.assertEqual(bundle["scene"]["provenance"]["input_hash"], trajectory["input_hash"])
                    artifacts.append(bundle)
                baseline, candidate = artifacts
                self.assertNotEqual(baseline["route"]["points_m"], candidate["route"]["points_m"])
                self.assertNotEqual(baseline["scene"]["obstacle_mask"], candidate["scene"]["obstacle_mask"])
                resolved = next(row for row in run["comparison"]["candidates"] if row["candidate_id"] == run["views"][1])
                self.assertEqual(resolved["changes"], [{"facility_id": "F1", "center": [18.5, 5]}])
                baseline, candidate = baseline["trajectory"], candidate["trajectory"]
                self.assertNotEqual(baseline["input_hash"], candidate["input_hash"])
                self.assertEqual(baseline["initial_state_hash"], candidate["initial_state_hash"])
                self.assertEqual(baseline["simulation_settings"], candidate["simulation_settings"])
            finally:
                api.close()

    def test_workbench_reruns_edited_geometry_without_hash_drift(self) -> None:
        from importlib.util import find_spec

        if find_spec("numpy") is None:
            self.skipTest("requires the A module's NumPy dependency")
        fixture_root = Path(os.environ["CROWDTURB_TEAM_FIXTURES"])
        with tempfile.TemporaryDirectory() as temp:
            api = ApiServerHarness(Path(temp) / "workbench.sqlite3", fixture_root)
            try:
                status, payload = api.request("GET", "/api/workbench/scene")
                self.assertEqual(status, 200, payload)
                scene = payload["scene"]

                def run():
                    status, payload = api.request("POST", "/api/workbench/runs", {"scene": scene})
                    self.assertEqual(status, 202, payload)
                    self.assertEqual(payload["run"]["status"], "queued")
                    return self.wait_for_preview(api, payload["run"]["run_id"])["run"]

                original = run()
                scene["params"]["jam_density"] = int(scene["params"]["jam_density"])
                self.assertEqual(run()["input_hash"], original["input_hash"])

                scene["meta"]["revision"] = 2
                scene["exits"][0]["width"] = 7.0
                scene["crowd"]["zones"][0]["count"] = 5
                variable = next(item for item in scene["variable_parameters"] if item["type"] == "exit_width")
                variable["current_value"] = 7.0
                variable["scene_diff"][0]["width"] = 7.0
                changed = run()
                self.assertNotEqual(changed["input_hash"], original["input_hash"])
                view_url = f"/api/workbench/runs/{changed['run_id']}/views/baseline"
                status, manifest = api.request("GET", view_url)
                self.assertEqual(status, 200, manifest)
                self.assertEqual((manifest["schema_version"], manifest["source"]), ("1.0.0", "mock"))
                self.assertEqual(set(manifest["artifacts"]), {"scene", "trajectory", "field", "analysis"})
                status, bundle = api.request("GET", manifest["artifacts"]["scene"])
                self.assertEqual(status, 200, bundle)
                self.assertEqual(bundle["exits"][0]["width_m"], 7)
                self.assertEqual(bundle["scene_revision"], 2)
                self.assertEqual(bundle["provenance"]["input_hash"], changed["input_hash"])
                self.assertEqual(api.request("GET", f"{view_url}/analysis")[1]["input_hash"], changed["input_hash"])
                trajectory = api.request("GET", manifest["artifacts"]["trajectory"])[1]
                self.assertEqual(len(trajectory["frames"][0]["agents"]), 5)
                for view in changed["views"]:
                    other = api.request("GET", f"/api/workbench/runs/{changed['run_id']}/views/{view}/trajectory")[1]
                    self.assertEqual(other["initial_state_hash"], trajectory["initial_state_hash"])
            finally:
                api.close()

    def test_workbench_failure_is_preserved_after_restart(self) -> None:
        fixture_root = Path(os.environ["CROWDTURB_TEAM_FIXTURES"])
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "workbench.sqlite3"
            api = ApiServerHarness(database, fixture_root)
            try:
                status, payload = api.request("POST", "/api/workbench/runs", {"scene": {"version": "unknown"}})
                self.assertEqual(status, 202)
                run_id = payload["run"]["run_id"]
                failed = self.wait_for_preview(api, run_id)
                self.assertEqual(failed["run"]["status"], "failed")
                self.assertEqual(failed["run"]["error"]["code"], "preview_failed")
            finally:
                api.close()

            api = ApiServerHarness(database)
            try:
                self.assertEqual(api.request("GET", f"/api/workbench/runs/{run_id}"), (200, failed))
                status, payload = api.request("GET", f"/api/workbench/runs/{run_id}/views/baseline")
                self.assertEqual(status, 409)
                self.assertEqual(payload["error"]["code"], "preview_unavailable")
            finally:
                api.close()

    def wait_for_preview(self, api, run_id):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            status, payload = api.request("GET", f"/api/workbench/runs/{run_id}")
            self.assertEqual(status, 200, payload)
            if payload["run"]["status"] in ("succeeded", "failed"):
                return payload
            time.sleep(0.1)
        self.fail(f"workbench run did not finish: {run_id}")

    def test_mock_artifacts_round_trip_and_keep_comparison_separate(self) -> None:
        fixture_root = Path(os.environ["CROWDTURB_TEAM_FIXTURES"])
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "review.sqlite3"
            api = ApiServerHarness(database, fixture_root)
            try:
                records = {}
                for kind in ("playback", "comparison"):
                    status, payload = api.request("POST", "/api/review-runs", {"kind": kind})
                    self.assertEqual(status, 201, payload)
                    records[kind] = payload["run"]
                self.assertNotEqual(records["playback"]["scene_id"], records["comparison"]["scene_id"])
            finally:
                api.close()

            api = ApiServerHarness(database)
            try:
                with urlopen(f"{api.url}/review", timeout=5) as response:
                    self.assertIn(b"/review.js", response.read())
                for record in records.values():
                    status, payload = api.request("GET", f"/api/review-runs/{record['run_id']}")
                    self.assertEqual((status, payload["run"]), (200, record))
                names = {
                    "trajectory": "trajectory.mock.json",
                    "derived-field": "field-from-trajectory.mock.json",
                    "analysis": "analysis.mock.json",
                    "comparison": "comparison.mock.json",
                }
                for record in records.values():
                    for name, artifact in record["artifacts"].items():
                        expected = load_json(fixture_root / "examples" / names[name])
                        self.assertEqual(api.request("GET", artifact["url"]), (200, expected))
                invalid_url = f"/api/review-runs/{records['comparison']['run_id']}/artifacts/analysis"
                self.assertEqual(api.request("GET", invalid_url)[0], 404)
            finally:
                api.close()

    def test_mismatched_input_hash_creates_no_record(self) -> None:
        fixture_root = Path(os.environ["CROWDTURB_TEAM_FIXTURES"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(fixture_root / "contracts", root / "contracts")
            shutil.copytree(fixture_root / "examples", root / "examples")
            path = root / "examples/analysis.mock.json"
            analysis = load_json(path)
            analysis["input_hash"] = "0" * 64
            path.write_text(json.dumps(analysis), encoding="utf-8")

            api = ApiServerHarness(root / "review.sqlite3", root)
            try:
                status, payload = api.request("POST", "/api/review-runs", {"kind": "playback"})
                self.assertEqual(status, 422)
                self.assertEqual(payload["error"]["code"], "invalid_review_sample")
                self.assertEqual(api.request("GET", "/api/review-runs"), (200, {"runs": []}))
            finally:
                api.close()


class PersistenceTests(unittest.TestCase):
    def test_result_survives_service_restart_and_queued_work_is_failed_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "persistent.sqlite3"
            service = AppService(database, seed_demo=False)
            created = service.create_project({"name": "Persistent project"})
            project_id = created["project"]["project_id"]
            task = service.create_task(project_id, "mock")
            deadline = time.monotonic() + 5
            while service.get_task(task["task_id"])["status"] not in ("succeeded", "failed"):
                if time.monotonic() > deadline:
                    self.fail("mock task did not finish")
                time.sleep(0.04)
            completed = service.get_task(task["task_id"])
            self.assertEqual(completed["status"], "succeeded")
            service.close()

            reopened = AppService(database, seed_demo=False)
            try:
                detail = reopened.get_project_detail(project_id)
                self.assertEqual(detail["project"]["name"], "Persistent project")
                self.assertEqual(detail["tasks"][0]["result_id"], completed["result_id"])
                self.assertEqual(reopened.get_result(completed["result_id"])["source"], "mock")
            finally:
                reopened.close()

    def test_interrupted_task_is_marked_failed_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "interrupted.sqlite3"
            service = AppService(database, seed_demo=False)
            created = service.create_project({"name": "Interrupted project"})
            project_id = created["project"]["project_id"]
            scene = service.get_scene(project_id)
            task = {
                "schema_version": "0.1.0",
                "task_id": "task-interrupted",
                "scene_id": scene["scene_id"],
                "scene_revision": scene["revision"],
                "source": "mock",
                "status": "running",
                "result_id": None,
                "error": None,
            }
            service.store.create_task(task, project_id, "2026-11-20T00:00:00.000Z")
            service.close()

            reopened = AppService(database, seed_demo=False)
            try:
                recovered = reopened.get_task("task-interrupted")
                self.assertEqual(recovered["status"], "failed")
                self.assertEqual(recovered["error"]["code"], "task_interrupted")
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
