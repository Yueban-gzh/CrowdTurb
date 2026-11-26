"""Small HTTP boundary around the system service.

The stdlib server is a W1 bootstrap so A-D can integrate without waiting for a
framework decision. Replace this adapter, not the service API, after BOOT-08.
"""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .repository import WEB_ROOT
from .service import ApiError, AppService
from .team_assets import B_FILES, embedded_policy

MAX_BODY_BYTES = 2 * 1024 * 1024
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/review": ("review.html", "text/html; charset=utf-8"),
    "/review.js": ("review.js", "text/javascript; charset=utf-8"),
    "/workbench": ("workbench.html", "text/html; charset=utf-8"),
    "/workbench.js": ("workbench.js", "text/javascript; charset=utf-8"),
    "/workbench.css": ("workbench.css", "text/css; charset=utf-8"),
    "/editor-bridge.js": ("editor-bridge.js", "text/javascript; charset=utf-8"),
}


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def make_handler(service: AppService, static_root: Path, viewer_root: Path | None = None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            try:
                path = urlsplit(self.path).path
                if method == "GET" and viewer_root is not None and path in ("/", "/index.html"):
                    self._static("/workbench")
                    return
                if method == "GET" and path.startswith(("/editor/", "/viewer/")):
                    self._team_asset(path)
                    return
                if path in STATIC_FILES and method == "GET":
                    self._static(path)
                    return
                if path == "/favicon.ico" and method == "GET":
                    self._send_bytes(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
                    return
                if not path.startswith("/api/"):
                    raise ApiError(404, "not_found", "请求的页面或接口不存在。")
                payload = self._json_body() if method == "POST" else None
                data, status = self._api(method, path, payload)
                self._json(status, data)
            except ApiError as exc:
                self._json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:  # pragma: no cover - final HTTP boundary
                print(f"Unhandled HTTP error: {type(exc).__name__}: {exc}", file=sys.stderr)
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": {"code": "internal_error", "message": "服务内部错误。"}},
                )

        def _api(self, method: str, path: str, payload: dict | None) -> tuple[dict, int]:
            parts = [unquote(part) for part in path.split("/") if part]

            if method == "GET" and parts == ["api", "health"]:
                return service.health(), HTTPStatus.OK

            if parts == ["api", "review-runs"]:
                if method == "GET":
                    runs = [run for run in service.store.list_review_runs() if run["kind"] != "workbench"]
                    return {"runs": runs}, HTTPStatus.OK
                return {"run": service.import_review_run(payload.get("kind"))}, HTTPStatus.CREATED

            if method == "GET" and parts[:2] == ["api", "review-runs"]:
                if len(parts) == 3:
                    record, _ = service.get_review_run(parts[2])
                    return {"run": record}, HTTPStatus.OK
                if len(parts) == 5 and parts[3] == "artifacts":
                    _, artifacts = service.get_review_run(parts[2])
                    if parts[4] not in artifacts:
                        raise ApiError(404, "review_artifact_not_found", "这条记录没有该产物。")
                    return artifacts[parts[4]], HTTPStatus.OK

            if parts == ["api", "workbench", "scene"] and method == "GET":
                return {"scene": service.get_workbench_scene()}, HTTPStatus.OK
            if parts == ["api", "workbench", "runs"]:
                if method == "POST":
                    return {"run": service.create_preview(payload.get("scene"))}, HTTPStatus.ACCEPTED
                return {"runs": service.list_previews()}, HTTPStatus.OK
            if parts[:3] == ["api", "workbench", "runs"] and method == "GET" and len(parts) >= 4:
                record, artifacts = service.get_preview(parts[3])
                if len(parts) == 4:
                    return {"run": record, "scene": artifacts["scene"]}, HTTPStatus.OK
                if len(parts) in (6, 7) and parts[4] == "views":
                    if record["status"] != "succeeded":
                        raise ApiError(409, "preview_unavailable", "这次评估还没有可播放的结果。")
                    view = artifacts["views"].get(parts[5], {})
                    if view and len(parts) == 6:
                        prefix = f"/api/workbench/runs/{parts[3]}/views/{parts[5]}"
                        return {
                            "schema_version": "1.0.0",
                            "run_id": record["run_id"],
                            "view": parts[5],
                            "source": record["source"],
                            "artifacts": {name: f"{prefix}/{name}" for name in view},
                        }, HTTPStatus.OK
                    if len(parts) == 7 and parts[6] in view:
                        return view[parts[6]], HTTPStatus.OK
                raise ApiError(404, "preview_artifact_not_found", "未找到该预演产物。")

            if parts == ["api", "projects"]:
                if method == "GET":
                    return {"projects": service.store.list_projects()}, HTTPStatus.OK
                if method == "POST":
                    return service.create_project(payload or {}), HTTPStatus.CREATED

            if len(parts) == 3 and parts[:2] == ["api", "projects"] and method == "GET":
                return service.get_project_detail(parts[2]), HTTPStatus.OK

            if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "scenes":
                project_id, revision_text = parts[2], parts[4]
                if method != "GET":
                    raise ApiError(405, "method_not_allowed", "该场景接口只支持 GET。")
                revision = None
                if revision_text != "latest":
                    try:
                        revision = int(revision_text)
                    except ValueError as exc:
                        raise ApiError(400, "invalid_revision", "场景版本必须是整数或 latest。") from exc
                return {"scene": service.get_scene(project_id, revision)}, HTTPStatus.OK

            if len(parts) == 4 and parts[:2] == ["api", "projects"]:
                project_id, collection = parts[2], parts[3]
                if collection == "scenes" and method == "POST":
                    scene = payload.get("scene") if isinstance(payload, dict) else None
                    if scene is None and isinstance(payload, dict):
                        scene = payload
                    return {"scene": service.save_scene(project_id, scene)}, HTTPStatus.CREATED
                if collection == "tasks":
                    if method == "GET":
                        return {"tasks": service.list_tasks(project_id)}, HTTPStatus.OK
                    if method == "POST":
                        source = payload.get("source") if isinstance(payload, dict) else None
                        revision = payload.get("scene_revision") if isinstance(payload, dict) else None
                        if revision is not None and not isinstance(revision, int):
                            raise ApiError(400, "invalid_revision", "scene_revision 必须是整数。")
                        return {
                            "task": service.create_task(project_id, source, revision)
                        }, HTTPStatus.ACCEPTED
                if collection == "results" and method == "GET":
                    return {"results": service.list_results(project_id)}, HTTPStatus.OK

            if len(parts) == 3 and parts[:2] == ["api", "tasks"] and method == "GET":
                return {"task": service.get_task(parts[2])}, HTTPStatus.OK

            if len(parts) == 3 and parts[:2] == ["api", "results"] and method == "GET":
                return {"result": service.get_result(parts[2])}, HTTPStatus.OK

            if method not in ("GET", "POST"):
                raise ApiError(405, "method_not_allowed", "不支持该 HTTP 方法。")
            raise ApiError(404, "not_found", "请求的接口不存在。")

        def _json_body(self) -> dict:
            length_text = self.headers.get("Content-Length", "0")
            try:
                length = int(length_text)
            except ValueError as exc:
                raise ApiError(400, "invalid_content_length", "Content-Length 无效。") from exc
            if length < 0 or length > MAX_BODY_BYTES:
                raise ApiError(413, "request_too_large", "请求体超过 2 MiB 限制。")
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ApiError(400, "invalid_json", f"请求体不是有效 JSON：{exc}") from exc
            if not isinstance(payload, dict):
                raise ApiError(400, "invalid_request", "请求体必须是 JSON 对象。")
            return payload

        def _static(self, path: str) -> None:
            filename, content_type = STATIC_FILES[path]
            file_path = static_root / filename
            try:
                content = file_path.read_bytes()
            except OSError as exc:
                raise ApiError(500, "static_asset_missing", f"缺少页面资源 {filename}。") from exc
            self._send_bytes(HTTPStatus.OK, content, content_type)

        def _team_asset(self, path: str) -> None:
            policy = None
            try:
                if path == "/editor/index.html" and service.team_fixtures:
                    source = service.team_fixtures / "apps/web/scene-config/editor/editor.html"
                    html = source.read_text(encoding="utf-8").replace(
                        "</body>", '<script src="/editor-bridge.js"></script></body>', 1
                    )
                    content_type = "text/html; charset=utf-8"
                    content = html.encode("utf-8")
                    policy = embedded_policy(html)
                elif viewer_root and path.startswith("/viewer/") and path[8:] in B_FILES:
                    name = path[8:]
                    source = viewer_root / "apps/web/prototype" / name
                    content_type = B_FILES[name]
                    content = source.read_bytes()
                    if name == "index.html":
                        policy = embedded_policy(content.decode("utf-8"))
                else:
                    raise ApiError(404, "team_asset_not_found", "未配置团队模块资源。")
            except (OSError, ValueError) as error:
                raise ApiError(503, "team_asset_unavailable", str(error)) from error
            self._send_bytes(HTTPStatus.OK, content, content_type, policy=policy)

        def _json(self, status: int, payload: dict) -> None:
            content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send_bytes(status, content, "application/json; charset=utf-8")

        def _send_bytes(self, status: int, content: bytes, content_type: str, *, policy=None) -> None:
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN" if policy else "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                policy or "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            if content:
                self.wfile.write(content)

        def log_message(self, format: str, *args) -> None:
            print(f"{self.address_string()} - {format % args}")

    return Handler


class ApiServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        database_path: str | Path = "data/crowdturb.sqlite3",
        static_root: Path = WEB_ROOT,
        seed_demo: bool = True,
        team_fixtures: Path | None = None,
        viewer_root: Path | None = None,
    ) -> None:
        self.service = AppService(database_path, seed_demo=seed_demo, team_fixtures=team_fixtures)
        self.httpd = ThreadingHTTPServer((host, port), make_handler(self.service, static_root, viewer_root))
        self.httpd.daemon_threads = True

    @property
    def host(self) -> str:
        return str(self.httpd.server_address[0])

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def serve_forever(self) -> None:
        self.httpd.serve_forever(poll_interval=0.25)

    def close(self) -> None:
        self.httpd.server_close()
        self.service.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="CrowdTurb system-base server (W1 Mock integration)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="data/crowdturb.sqlite3")
    parser.add_argument("--no-demo", action="store_true", help="Do not create the Mock demo project.")
    parser.add_argument("--team-fixtures", type=Path, help="D checkout root containing examples/ and contracts/.")
    parser.add_argument("--viewer-root", type=Path, help="B checkout root containing apps/web/prototype/.")
    args = parser.parse_args()

    server = ApiServer(
        host=args.host,
        port=args.port,
        database_path=args.db,
        seed_demo=not args.no_demo,
        team_fixtures=args.team_fixtures,
        viewer_root=args.viewer_root,
    )
    print(f"CrowdTurb system base: {server.url}")
    print(f"SQLite: {Path(args.db).resolve()}")
    print("Data source: hand-authored Mock fixture only; no PDE/FNO execution is available yet.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CrowdTurb system base.")
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
