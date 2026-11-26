# -*- coding: utf-8 -*-
"""ArchFlow AI —— A 模块导出工具（命令行）。

用途：
    1) C/PDE 侧拿到计算输入：  python export_rho.py ../scenes/gym_example.json
       → 输出 output/gym_example_v0.1.npz（rho0 / walkable / zone_id / 参数）
    2) D/优化侧批量评价候选：改 JSON 里的出口宽度等参数后重跑本脚本即可，无需打开编辑器。
    3) 预览图（可选）：        加 --preview，需要 matplotlib。

退出码：0=校验通过已导出；1=有 error 未导出。供 E 的任务调度直接判断。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scene_io import (load_scene, rasterize, total_people, validate, export_npz,
                      _facilities, _scene_revision)


def _preview(npz_path: Path, png_path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import numpy as np
        import matplotlib.pyplot as plt
        # Windows 自带微软雅黑；其他系统按顺序回退
        matplotlib.rcParams["font.sans-serif"] = \
            ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "PingFang SC", "DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except ImportError:
        print("（未安装 matplotlib，跳过预览图）")
        return False
    d = np.load(npz_path, allow_pickle=False)
    rho, walk, dx = d["rho0"], d["walkable"], float(d["dx"])
    fig, ax = plt.subplots(figsize=(10, 10 * rho.shape[0] / rho.shape[1]))
    bg = np.where(walk, np.nan, 1.0)                 # 障碍/墙画深灰
    ax.imshow(bg, origin="lower", cmap="gray", alpha=0.85)
    im = ax.imshow(np.where(walk, rho, np.nan), origin="lower", cmap="YlOrRd")
    fig.colorbar(im, ax=ax, label="初始密度 (人/m²)")
    total = float(np.nan_to_num(rho).sum()) * dx * dx
    mask = str(d["mask_hash"])[:12] if "mask_hash" in d else "-"
    ax.set_title(f"初始密度场 rho0 — {npz_path.stem}\n总人数 {total:.0f}   "
                 f"网格 {rho.shape[1]}x{rho.shape[0]} @ {dx}m   mask {mask}")
    ax.set_xticks([]); ax.set_yticks([])
    fig.savefig(png_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="场景校验 + 初始密度场导出（A→C/D 的交接工具）")
    ap.add_argument("scene", help="场景 JSON 路径")
    ap.add_argument("--out", default=None, help="输出 .npz 路径（默认 output/<场景名>_v<版本>.npz）")
    ap.add_argument("--preview", action="store_true", help="同时输出密度预览 PNG（需 matplotlib）")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    scene_path = Path(args.scene)
    scene = load_scene(scene_path)
    stem = scene["meta"]["name"].replace(" ", "_")
    version = str(scene.get("version", ""))
    out_path = Path(args.out) if args.out else \
        scene_path.parent / "output" / f"{stem}_v{version}.npz"

    problems = validate(scene)
    errors = [p for p in problems if p["level"] == "error"]
    warnings = [p for p in problems if p["level"] == "warning"]

    R = rasterize(scene)
    print(f"场景『{scene['meta']['name']}』({scene_path.name}, v{version}, "
          f"rev {_scene_revision(scene)})")
    print(f"  网格: {R['Ny']} x {R['Nx']} 格 @ {R['res']} m/格    出口: {len(scene['exits'])} 个    "
          f"人群区: {len(scene['crowd']['zones'])} 个    设施: {len(_facilities(scene))} 个    "
          f"总人数: {total_people(R['rho0'], R['res']):.0f}")
    print(f"  掩码 mask_hash={R['mask_hash'][:12]}  输入 input_hash={R['input_hash'][:12]}")
    for p in problems:
        print(f"  [{p['level'].upper()}] {p['msg']}")
    if not problems:
        print("  校验通过，无问题。")

    if errors:
        print("存在 error，未导出。请先修复。")
        return 1

    export_npz(scene, out_path)
    print(f"已导出: {out_path}")
    print(f"  C 读取方式: np.load('{out_path.name}') → rho0 (Ny,Nx 人/m²), walkable, "
          f"obstacle_mask, zone_id, dx, mask_hash, input_hash")
    if args.preview:
        png = out_path.with_suffix(".png")
        if _preview(out_path, png):
            print(f"预览图: {png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
