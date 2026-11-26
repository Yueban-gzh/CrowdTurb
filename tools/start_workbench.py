"""Prepare the pinned team versions and start the local integration workbench."""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.crowdturb.team_assets import B_FILES, viewer_page, viewer_script

D_REVISION = "769b196ae2eecd9c1b51c85d535571a2c2f9d791"
B_REVISION = "2cf04544c6e535fccb5f103687f10d6ee45d5f1c"


def snapshot(revision, expected_file):
    cache = ROOT / "output"
    cache.mkdir(exist_ok=True)
    target = cache / f"CrowdTurb-{revision}"
    if (target / expected_file).is_file():
        return target
    if target.exists():
        raise ValueError(f"快照目录不完整，请移走后重试：{target}")

    with tempfile.TemporaryDirectory(dir=cache) as directory:
        staging = Path(directory)
        archive = staging / "team.zip"
        url = f"https://codeload.github.com/Yueban-gzh/CrowdTurb/zip/{revision}"
        print(f"获取团队版本 {revision[:7]}", file=sys.stderr, flush=True)
        with urlopen(url, timeout=40) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with ZipFile(archive) as source:
            source.extractall(staging)
        unpacked = staging / target.name
        if not (unpacked / expected_file).is_file():
            raise ValueError(f"团队版本缺少 {expected_file}")
        unpacked.rename(target)
    return target


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
    parser.add_argument("--prepare-only", action="store_true", help="Prepare assets and print environment assignments")
    args = parser.parse_args()
    try:
        import numpy

        team = snapshot(D_REVISION, "tools/run_analysis.py")
        viewer = args.native_viewer.resolve() if args.native_viewer else prepare_viewer(
            snapshot(B_REVISION, "apps/web/prototype/main.js")
        )
        if not (viewer / "apps/web/prototype/index.html").is_file():
            raise ValueError(f"B 页面缺失：{viewer}")
    except ImportError as error:
        parser.exit(1, f"{error}。请先安装 requirements-workbench.txt 中的依赖。\n")
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
    try:
        return subprocess.run(argv, cwd=ROOT, check=False).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
