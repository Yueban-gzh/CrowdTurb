"""Application service for the E-owned task and persistence foundation."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import ValidationError

from .adapters import SolverNotConfigured, build_adapters
from .repository import FIXTURES, load_json, validate_scene, validate_task
from .review import SAMPLES, load_review_sample
from .store import Store
from .workbench import initial_scene, run_preview

LOGGER = logging.getLogger(__name__)
VALID_SOURCES = ("mock", "pde", "fno")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AppService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        fixtures: Path = FIXTURES,
        seed_demo: bool = False,
        team_fixtures: Path | None = None,
    ) -> None:
        self.store = Store(database_path)
        self.fixtures = fixtures
        self.team_fixtures = team_fixtures
        self.adapters = build_adapters(fixtures)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crowdturb-task")
        self._recover_interrupted_tasks()
        for record in self.list_previews():
            if record["status"] in ("queued", "running"):
                self._fail_preview(record, "task_interrupted", "服务在评估完成前重启，请重新提交。")
        if seed_demo:
            self.ensure_demo_project()

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)

    def health(self) -> dict:
        adapters = {
            source: ("not_configured" if source != "mock" else "ready")
            for source in VALID_SOURCES
        }
        return {
            "status": "ok",
            "service": "crowdturb-api",
            "task_state_model": ["queued", "running", "succeeded", "failed"],
            "adapters": adapters,
        }

    def import_review_run(self, kind: str) -> dict:
        if not isinstance(kind, str) or kind not in SAMPLES:
            raise ApiError(400, "unknown_review_sample", "请选择轨迹回放或方案比较样例。")
        if self.team_fixtures is None:
            raise ApiError(503, "review_samples_not_configured", "请使用 --team-fixtures 指定 D 的源码快照目录。")
        try:
            identity, artifacts = load_review_sample(self.team_fixtures, kind)
        except OSError as exc:
            raise ApiError(503, "review_samples_unavailable", "D 样例或契约缺失，请核对快照目录。") from exc
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            message = exc.message if isinstance(exc, ValidationError) else str(exc)
            raise ApiError(422, "invalid_review_sample", message) from exc

        run_id = f"review-{uuid.uuid4().hex}"
        record = {
            "run_id": run_id,
            "kind": kind,
            "source": "mock",
            "created_at": utc_now(),
            **identity,
            "artifacts": {
                name: {
                    "schema_version": payload["schema_version"],
                    "url": f"/api/review-runs/{run_id}/artifacts/{name}",
                }
                for name, payload in artifacts.items()
            },
        }
        self.store.save_review_run(record, artifacts)
        return record

    def get_review_run(self, run_id: str) -> tuple[dict, dict]:
        run = self.store.get_review_run(run_id)
        if run is None:
            raise ApiError(404, "review_run_not_found", "未找到这条联调记录。")
        return run

    def get_workbench_scene(self) -> dict:
        if self.team_fixtures is None:
            raise ApiError(503, "team_modules_unavailable", "请配置团队模块目录。")
        return initial_scene(self.team_fixtures)

    def create_preview(self, scene: dict) -> dict:
        if self.team_fixtures is None:
            raise ApiError(503, "team_modules_unavailable", "请配置团队模块目录。")
        if not isinstance(scene, dict):
            raise ApiError(400, "invalid_scene", "缺少编辑器场景。")
        meta = scene.get("meta") if isinstance(scene.get("meta"), dict) else {}
        now = utc_now()
        record = {
            "run_id": f"preview-{uuid.uuid4().hex}",
            "kind": "workbench",
            "source": "mock",
            "created_at": now,
            "updated_at": now,
            "status": "queued",
            "error": None,
            "scene_name": meta.get("name") or "未命名布局",
            "scene_revision": meta.get("revision"),
            "views": [],
        }
        self.store.save_review_run(record, {"scene": scene, "views": {}})
        self.executor.submit(self._run_preview, record, scene)
        return record

    def _run_preview(self, queued: dict, scene: dict) -> None:
        record = {**queued, "status": "running", "updated_at": utc_now()}
        self.store.update_review_run(record)
        try:
            preview = run_preview(self.team_fixtures, scene)
            baseline = preview["views"]["baseline"]["trajectory"]
            completed = {
                **record,
                "status": "succeeded",
                "updated_at": utc_now(),
                "source": baseline["source"],
                "scene_id": baseline["scene_id"],
                "scene_name": preview["scene"]["meta"]["name"],
                "scene_revision": baseline["scene_revision"],
                "input_hash": baseline["input_hash"],
                "views": list(preview["views"]),
                "comparison": preview["comparison"],
                "problems": preview["problems"],
            }
            self.store.update_review_run(completed, {"scene": preview["scene"], "views": preview["views"]})
        except ValueError as error:
            self._fail_preview(record, "preview_failed", str(error))
        except TimeoutError:
            self._fail_preview(record, "preview_timeout", "评估超时，请缩小场景或候选范围后重试。")
        except Exception:
            LOGGER.exception("Unexpected layout assessment error")
            self._fail_preview(record, "internal_error", "评估执行异常。")

    def _fail_preview(self, record: dict, code: str, message: str) -> None:
        self.store.update_review_run({
            **record, "status": "failed", "updated_at": utc_now(),
            "error": {"code": code, "message": message},
        })

    def list_previews(self) -> list[dict]:
        return [
            {"status": "succeeded", "error": None, **run}
            for run in self.store.list_review_runs() if run["kind"] == "workbench"
        ]

    def get_preview(self, run_id: str) -> tuple[dict, dict]:
        run = self.store.get_review_run(run_id)
        if run is None or run[0]["kind"] != "workbench":
            raise ApiError(404, "preview_not_found", "未找到预演记录。")
        record, artifacts = run
        return {"status": "succeeded", "error": None, **record}, artifacts

    def ensure_demo_project(self) -> dict:
        projects = self.store.list_projects()
        if projects:
            return projects[0]
        return self.create_project({"name": "体育馆示例（Mock 联调）"})["project"]

    def create_project(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid_request", "请求体必须是 JSON 对象。")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ApiError(400, "invalid_project_name", "项目名称必须为 1–120 个字符。")

        scene = payload.get("scene")
        if scene is None:
            scene = copy.deepcopy(load_json(self.fixtures / "scene.mock.json"))
        if not isinstance(scene, dict):
            raise ApiError(400, "invalid_scene", "scene 必须是 JSON 对象。")
        scene = copy.deepcopy(scene)
        scene["revision"] = 1
        self._require_valid_scene(scene)

        project_id = f"prj-{uuid.uuid4().hex[:12]}"
        now = utc_now()
        self.store.create_project(project_id, name.strip(), scene, digest(scene), now)
        return {
            "project": self.store.get_project(project_id),
            "scene": scene,
        }

    def get_project_detail(self, project_id: str) -> dict:
        project = self.store.get_project(project_id)
        if project is None:
            raise ApiError(404, "project_not_found", f"项目 {project_id} 不存在。")
        scene = self.store.get_scene(project_id)
        if scene is None:
            raise ApiError(500, "scene_missing", "项目已存在但没有可读取的场景。")
        return {
            "project": project,
            "scene": scene,
            "tasks": self.store.list_tasks(project_id),
            "results": self.store.list_results(project_id),
        }

    def get_scene(self, project_id: str, revision: int | None = None) -> dict:
        self._require_project(project_id)
        scene = self.store.get_scene(project_id, revision)
        if scene is None:
            detail = "最新场景" if revision is None else f"场景版本 {revision}"
            raise ApiError(404, "scene_not_found", f"项目 {project_id} 的{detail}不存在。")
        return scene

    def save_scene(self, project_id: str, scene: dict) -> dict:
        self._require_project(project_id)
        current = self.store.get_scene(project_id)
        if current is None:
            raise ApiError(500, "scene_missing", "项目没有当前场景，无法保存新版本。")
        if not isinstance(scene, dict):
            raise ApiError(400, "invalid_scene", "scene 必须是 JSON 对象。")
        scene = copy.deepcopy(scene)
        if scene.get("scene_id") != current.get("scene_id"):
            raise ApiError(
                409,
                "scene_id_mismatch",
                "保存新版本时不能修改 scene_id；请创建新项目表达另一个场馆。",
            )
        scene["revision"] = current["revision"] + 1
        self._require_valid_scene(scene)
        self.store.add_scene(project_id, scene, digest(scene), utc_now())
        return scene

    def create_task(
        self,
        project_id: str,
        source: str,
        scene_revision: int | None = None,
    ) -> dict:
        self._require_project(project_id)
        if source not in VALID_SOURCES:
            raise ApiError(400, "unsupported_source", f"source 必须是 {', '.join(VALID_SOURCES)}。")
        scene = self.store.get_scene(project_id, scene_revision)
        if scene is None:
            raise ApiError(404, "scene_not_found", "任务引用的场景版本不存在。")

        task = {
            "schema_version": "0.1.0",
            "task_id": f"task-{uuid.uuid4().hex[:12]}",
            "scene_id": scene["scene_id"],
            "scene_revision": scene["revision"],
            "source": source,
            "status": "queued",
            "result_id": None,
            "error": None,
        }
        try:
            validate_task(task, scene)
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            raise ApiError(400, "invalid_task", f"任务未通过契约检查：{exc}") from exc

        self.store.create_task(task, project_id, utc_now())
        self.executor.submit(self._run_task, task, scene, project_id)
        return task

    def get_task(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        if task is None:
            raise ApiError(404, "task_not_found", f"任务 {task_id} 不存在。")
        return task

    def list_tasks(self, project_id: str) -> list[dict]:
        self._require_project(project_id)
        return self.store.list_tasks(project_id)

    def list_results(self, project_id: str) -> list[dict]:
        self._require_project(project_id)
        return self.store.list_results(project_id)

    def get_result(self, result_id: str) -> dict:
        result = self.store.get_result(result_id)
        if result is None:
            raise ApiError(404, "result_not_found", f"结果 {result_id} 不存在。")
        return result

    def _run_task(self, queued_task: dict, scene: dict, project_id: str) -> None:
        running = copy.deepcopy(queued_task)
        running["status"] = "running"
        self.store.update_task(running, utc_now())

        try:
            adapter = self.adapters[queued_task["source"]]
            result = adapter.solve(scene, queued_task["task_id"])  # type: ignore[attr-defined]
            completed = copy.deepcopy(running)
            completed["status"] = "succeeded"
            completed["result_id"] = result["result_id"]
            completed["error"] = None
            validate_task(completed, scene, result)
            self.store.complete_task(completed, result, project_id, utc_now())
        except SolverNotConfigured as exc:
            self._fail_task(running, exc.code, str(exc))
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            self._fail_task(running, "result_contract_invalid", f"求解结果未通过契约检查：{exc}")
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            LOGGER.exception("Unhandled task worker error for %s", queued_task["task_id"])
            self._fail_task(running, "internal_error", f"任务执行异常：{type(exc).__name__}")

    def _fail_task(self, task: dict, code: str, message: str) -> None:
        failed = copy.deepcopy(task)
        failed["status"] = "failed"
        failed["result_id"] = None
        failed["error"] = {"code": code, "message": message}
        self.store.update_task(failed, utc_now())

    def _recover_interrupted_tasks(self) -> None:
        for task in self.store.list_unfinished_tasks():
            self._fail_task(
                task,
                "task_interrupted",
                "服务在任务完成前重启；本次运行已标记失败。请新建任务重试。",
            )

    def _require_project(self, project_id: str) -> dict:
        project = self.store.get_project(project_id)
        if project is None:
            raise ApiError(404, "project_not_found", f"项目 {project_id} 不存在。")
        return project

    @staticmethod
    def _require_valid_scene(scene: dict) -> None:
        try:
            validate_scene(scene)
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            raise ApiError(400, "invalid_scene", f"场景未通过契约检查：{exc}") from exc
