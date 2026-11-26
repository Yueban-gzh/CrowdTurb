"""Prepare the viewer from this checkout and start the integrated workbench."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.crowdturb.team_assets import B_FILES, viewer_page, viewer_script


def prepare_viewer(source_root):
    relative = Path("apps/web/prototype")
    source = source_root / relative
    target_root = ROOT / "output" / "workbench-viewer"
    target = target_root / relative
    main = viewer_script((source / "main.js").read_text(encoding="utf-8"))
    html = viewer_page((source / "index.html").read_text(encoding="utf-8"))
    target.mkdir(parents=True, exist_ok=True)
    for name in B_FILES.keys() - {"main.js", "index.html", "workbench-api.js"}:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    (target / "main.js").write_text(main, encoding="utf-8")
    (target / "index.html").write_text(html, encoding="utf-8")
    shutil.copyfile(ROOT / "apps/web/workbench-api.js", target / "workbench-api.js")
    return target_root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--db", type=Path, default=ROOT / "data/workbench.sqlite3")
    parser.add_argument("--native-viewer", type=Path, help="B checkout with native workbench API support")
    parser.add_argument("--onnx-model", type=Path, help="Enable single-floor STDDN with a local ONNX file")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare assets and print environment assignments")
    args = parser.parse_args()
    try:
        import numpy

        if args.onnx_model:
            if not args.onnx_model.is_file():
                raise ValueError(f"找不到本地 ONNX 文件：{args.onnx_model}")
            import onnxruntime

        team = ROOT
        for relative in ("packages/analysis/crowdturb_analysis/core.py",
                         "apps/web/scene-config/python/scene_io.py"):
            if not (team / relative).is_file():
                raise ValueError(f"当前仓库缺少模块：{relative}")
        viewer = args.native_viewer.resolve() if args.native_viewer else prepare_viewer(ROOT)
        if not (viewer / "apps/web/prototype/index.html").is_file():
            raise ValueError(f"B 页面缺失：{viewer}")
    except ImportError as error:
        requirements = "requirements-stddn.txt" if args.onnx_model else "requirements-workbench.txt"
        parser.exit(1, f"{error}。请先安装 {requirements} 中的依赖。\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"工作台准备失败：{error}\n")
    if args.prepare_only:
        print(f"CROWDTURB_TEAM_FIXTURES={team}")
        print(f"CROWDTURB_VIEWER_ROOT={viewer}")
        return 0
    argv = [
        sys.executable, "-m", "apps.api.crowdturb", "--port", str(args.port),
        "--db", str(args.db.resolve()), "--team-fixtures", str(team), "--viewer-root", str(viewer),
    ]
    if args.onnx_model:
        argv += ["--onnx-model", str(args.onnx_model.resolve())]
    try:
        return subprocess.run(argv, cwd=ROOT, check=False).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
