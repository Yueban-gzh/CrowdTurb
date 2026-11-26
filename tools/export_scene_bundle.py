# -*- coding: utf-8 -*-
"""A 场景（或 A 导出的 .npz）→ B 可读取的 JSON 包（scene-bundle）。

用途：
    A 的 rasterize()/export_npz() 产出的是 .npz 二进制，浏览器无法直接读取。
    本脚本把场景栅格化结果转成 JSON，供 apps/web/prototype 加载。

用法（在仓库根目录，且 A 模块已 checkout 到 apps/web/scene-config）：

    python tools/export_scene_bundle.py A场景文件.json -o 输出.json
    python tools/export_scene_bundle.py A导出的.npz   -o 输出.json

输出结构（schema_version 1.0.0，为 B 侧适配格式，不是共享契约）：
    {
      "schema_version": "1.0.0",
      "kind": "scene-bundle",
      "producer": "tools/export_scene_bundle.py",
      "scene_id": "...",          # 场景名
      "scene_revision": 1,
      "grid": {"nx","ny","dx","dy","origin"},
      "walkable": [[bool, ...], ...],       # [iy][ix]，原点左下角
      "initial_density": [[float, ...], ...],
      "obstacle_mask": [[bool, ...], ...],  # 障碍 + 设施（不含边界墙）
      "exits": [{"exit_id": "E1", "cells": [[ix,iy], ...], "width_m": 6.0}],
      "provenance": {"source_json": "...", "mask_hash": "...", "input_hash": "..."}
    }

重要：本格式只负责「场景几何」，不含场/轨迹。场与轨迹来自 C（trajectory），
分析结果来自 D（analysis），三者由 B 按各自契约分别读取。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
A_MODULE = REPO_ROOT / "apps" / "web" / "scene-config" / "python"


def _load_a_module():
    """导入 A 的 scene_io（不修改 A 代码，只复用其栅格化逻辑）。"""
    if not A_MODULE.is_dir():
        raise SystemExit(
            f"未找到 A 模块：{A_MODULE}\n"
            "请先把 A 的分支检出到 apps/web/scene-config（例如：\n"
            "  git checkout origin/feat/A-scene-config -- apps/web/scene-config\n"
            "）后重试。"
        )
    sys.path.insert(0, str(A_MODULE))
    try:
        import scene_io  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            f"导入 A 的 scene_io 失败：{exc}\n"
            "A 模块依赖 numpy，请先 `python -m pip install -r apps/web/scene-config/requirements.txt`。"
        ) from exc
    return scene_io


def _bool_grid(arr) -> list[list[bool]]:
    return [[bool(v) for v in row] for row in arr.tolist()]


def _float_grid(arr, ndigits: int = 6) -> list[list[float]]:
    return [[round(float(v), ndigits) for v in row] for row in arr.tolist()]


def from_scene_json(scene_path: Path, scene_io) -> dict:
    scene = scene_io.load_scene(scene_path)
    problems = scene_io.validate(scene)
    errors = [p["msg"] for p in problems if p["level"] == "error"]
    if errors:
        raise SystemExit("场景校验未通过，未导出：\n  - " + "\n  - ".join(errors))

    R = scene_io.rasterize(scene)
    exit_width = {ex["id"]: float(ex.get("width", 0.0)) for ex in scene["exits"]}
    return {
        "schema_version": "1.0.0",
        "kind": "scene-bundle",
        "producer": "tools/export_scene_bundle.py",
        "scene_id": str(scene["meta"]["name"]),
        "scene_revision": int(scene_io._scene_revision(scene)),
        "grid": {
            "nx": int(R["Nx"]),
            "ny": int(R["Ny"]),
            "dx": float(R["res"]),
            "dy": float(R["res"]),
            "origin": [0.0, 0.0],
        },
        "walkable": _bool_grid(R["walkable"]),
        "initial_density": _float_grid(R["rho0"]),
        "obstacle_mask": _bool_grid(R["obstacle_mask"]),
        "exits": [
            {
                "exit_id": exit_id,
                "cells": [[int(i), int(j)] for (i, j) in R["exit_cells"][exit_id]],
                "width_m": exit_width.get(exit_id, 0.0),
            }
            for exit_id in sorted(R["exit_cells"])
        ],
        "provenance": {
            "source": "scene-json",
            "source_path": str(scene_path.name),
            "scene_version": str(scene.get("version", "")),
            "mask_hash": R["mask_hash"],
            "input_hash": R["input_hash"],
            "walkable_hash": R["walkable_hash"],
        },
    }


def from_npz(npz_path: Path) -> dict:
    import numpy as np

    d = np.load(npz_path, allow_pickle=False)
    exit_cells = json.loads(str(d["exit_cells_json"]))
    scene = json.loads(str(d["scene_json"]))
    exit_width = {ex["id"]: float(ex.get("width", 0.0)) for ex in scene.get("exits", [])}
    dx = float(d["dx"])
    return {
        "schema_version": "1.0.0",
        "kind": "scene-bundle",
        "producer": "tools/export_scene_bundle.py",
        "scene_id": str(scene["meta"]["name"]),
        "scene_revision": int(d["scene_revision"]),
        "grid": {
            "nx": int(d["walkable"].shape[1]),
            "ny": int(d["walkable"].shape[0]),
            "dx": dx,
            "dy": dx,
            "origin": [0.0, 0.0],
        },
        "walkable": _bool_grid(d["walkable"]),
        "initial_density": _float_grid(d["rho0"]),
        "obstacle_mask": _bool_grid(d["obstacle_mask"]),
        "exits": [
            {
                "exit_id": exit_id,
                "cells": [[int(i), int(j)] for (i, j) in cells],
                "width_m": exit_width.get(exit_id, 0.0),
            }
            for exit_id, cells in sorted(exit_cells.items())
        ],
        "provenance": {
            "source": "npz",
            "source_path": str(npz_path.name),
            "scene_version": str(d["scene_version"]),
            "mask_hash": str(d["mask_hash"]),
            "input_hash": str(d["input_hash"]),
            "walkable_hash": str(d["walkable_hash"]),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="A 场景 → B 可读取的 scene-bundle JSON")
    ap.add_argument("input", help="A 的场景 JSON 或已导出的 .npz")
    ap.add_argument("-o", "--out", required=True, help="输出 JSON 路径")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        raise SystemExit(f"输入文件不存在：{src}")

    if src.suffix.lower() == ".npz":
        bundle = from_npz(src)
    else:
        bundle = from_scene_json(src, _load_a_module())

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    g = bundle["grid"]
    n_exit = len(bundle["exits"])
    total = sum(sum(row) for row in bundle["initial_density"]) * g["dx"] * g["dy"]
    size_kb = out.stat().st_size / 1024
    print(f"已导出 {out}")
    print(f"  场景 {bundle['scene_id']} rev {bundle['scene_revision']}  "
          f"网格 {g['nx']}x{g['ny']} @ {g['dx']}m  出口 {n_exit} 个  初始人数 {total:.0f}")
    print(f"  文件大小 {size_kb:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())