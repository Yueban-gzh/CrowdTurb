"""SQLite persistence for projects, scene revisions, tasks, and results."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scenes (
                    project_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    scene_id TEXT NOT NULL,
                    scene_json TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, revision),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS results (
                    result_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    scene_id TEXT NOT NULL,
                    scene_revision INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    scene_id TEXT NOT NULL,
                    scene_revision INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_id TEXT,
                    error_json TEXT,
                    task_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );

                CREATE INDEX IF NOT EXISTS idx_scenes_project_revision
                    ON scenes(project_id, revision DESC);
                CREATE INDEX IF NOT EXISTS idx_tasks_project_created
                    ON tasks(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_results_project_created
                    ON results(project_id, created_at DESC);
                """
            )

    def save_review_run(self, record: dict, artifacts: dict) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO review_runs VALUES (?, ?, ?, ?)",
                (record["run_id"], record["created_at"], _json(record), _json(artifacts)),
            )

    def list_review_runs(self) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT record_json FROM review_runs ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def update_review_run(self, record: dict, artifacts: dict | None = None) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE review_runs SET record_json = ?, artifacts_json = COALESCE(?, artifacts_json)
                WHERE run_id = ?
                """,
                (_json(record), _json(artifacts) if artifacts is not None else None, record["run_id"]),
            )

    def get_review_run(self, run_id: str) -> tuple[dict, dict] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT record_json, artifacts_json FROM review_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["record_json"]), json.loads(row["artifacts_json"])

    def create_project(
        self,
        project_id: str,
        name: str,
        scene: dict,
        scene_sha256: str,
        now: str,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO projects(project_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (project_id, name, now, now),
            )
            connection.execute(
                """
                INSERT INTO scenes(project_id, revision, scene_id, scene_json, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, scene["revision"], scene["scene_id"], _json(scene), scene_sha256, now),
            )

    def list_projects(self) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT p.project_id, p.name, p.created_at, p.updated_at,
                       COALESCE(MAX(s.revision), 0) AS current_revision
                FROM projects p
                LEFT JOIN scenes s ON s.project_id = p.project_id
                GROUP BY p.project_id
                ORDER BY p.updated_at DESC, p.project_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT p.project_id, p.name, p.created_at, p.updated_at,
                       COALESCE(MAX(s.revision), 0) AS current_revision
                FROM projects p
                LEFT JOIN scenes s ON s.project_id = p.project_id
                WHERE p.project_id = ?
                GROUP BY p.project_id
                """,
                (project_id,),
            ).fetchone()
        return dict(row) if row else None

    def add_scene(
        self,
        project_id: str,
        scene: dict,
        scene_sha256: str,
        now: str,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO scenes(project_id, revision, scene_id, scene_json, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, scene["revision"], scene["scene_id"], _json(scene), scene_sha256, now),
            )
            connection.execute(
                "UPDATE projects SET updated_at = ? WHERE project_id = ?",
                (now, project_id),
            )

    def get_scene(self, project_id: str, revision: int | None = None) -> dict | None:
        query = "SELECT scene_json FROM scenes WHERE project_id = ?"
        parameters: tuple = (project_id,)
        if revision is None:
            query += " ORDER BY revision DESC LIMIT 1"
        else:
            query += " AND revision = ?"
            parameters += (revision,)
        with closing(self._connect()) as connection:
            row = connection.execute(query, parameters).fetchone()
        return json.loads(row["scene_json"]) if row else None

    def create_task(self, task: dict, project_id: str, now: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, project_id, scene_id, scene_revision, source,
                    status, result_id, error_json, task_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["task_id"],
                    project_id,
                    task["scene_id"],
                    task["scene_revision"],
                    task["source"],
                    task["status"],
                    task["result_id"],
                    _json(task["error"]) if task["error"] is not None else None,
                    _json(task),
                    now,
                    now,
                ),
            )

    def update_task(self, task: dict, now: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, result_id = ?, error_json = ?, task_json = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    task["status"],
                    task["result_id"],
                    _json(task["error"]) if task["error"] is not None else None,
                    _json(task),
                    now,
                    task["task_id"],
                ),
            )

    def complete_task(self, task: dict, result: dict, project_id: str, now: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO results(
                    result_id, project_id, task_id, source, scene_id,
                    scene_revision, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["result_id"],
                    project_id,
                    task["task_id"],
                    result["source"],
                    result["scene_id"],
                    result["scene_revision"],
                    _json(result),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, result_id = ?, error_json = NULL, task_json = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (task["status"], task["result_id"], _json(task), now, task["task_id"]),
            )

    def get_task(self, task_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT task_json FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return json.loads(row["task_json"]) if row else None

    def list_tasks(self, project_id: str) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT task_json FROM tasks WHERE project_id = ? ORDER BY created_at DESC, task_id",
                (project_id,),
            ).fetchall()
        return [json.loads(row["task_json"]) for row in rows]

    def list_unfinished_tasks(self) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT task_json FROM tasks WHERE status IN ('queued', 'running') ORDER BY created_at"
            ).fetchall()
        return [json.loads(row["task_json"]) for row in rows]

    def get_result(self, result_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT result_json FROM results WHERE result_id = ?", (result_id,)
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def list_results(self, project_id: str) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT result_id, task_id, source, scene_id, scene_revision, created_at
                FROM results
                WHERE project_id = ?
                ORDER BY created_at DESC, result_id
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]
