# -*- coding: utf-8 -*-
"""生成 gym 规模（120x80, 1900 人）的 Mock 轨迹与分析样例，供 B 的页面演示。

⚠ 这是**合成演示数据**（source=mock），不是 STDDN 模型输出，不是真实疏散结果。
   用途：在 C 的真实轨迹到位前，让 B 的轨迹渲染与指标面板可以联调。

数据流向：
    A 的 scene-bundle（真实几何/掩码/人群分布）
      → 本脚本按人群分布撒初始位置
      → 朝最近出口做匀速直线流动 + 简单避障（不是物理模型）
      → 输出 contracts/trajectory.schema.json 兼容的 trajectory
      → 再用 D 的分析口径算一份 analysis（格子密度、残留人数、拥堵、热点）

用法（仓库根目录）：
    python tools/make_demo_trajectory.py apps/web/prototype/data/gym.bundle.json \
        -o apps/web/prototype/data/gym.trajectory.mock.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

DT = 0.5          # 记录帧间隔（秒）。真实模型推理步长 0.08，此处降低以控制数据量
HORIZON = 60.0    # 模拟时长（秒）
SEED = 7
SUBSAMPLE = 6     # 每 N 个人保留 1 个进轨迹文件（1900 → 约 320 人），控制 JSON 体积
THRESHOLD = 3.0   # 拥堵密度阈值 人/m²（演示值，真实阈值待 D 确认）


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def nearest_exit_cell(exit_cells, x, y, dx, dy):
    best, best_d = None, float("inf")
    for (ix, iy) in exit_cells:
        cx, cy = (ix + 0.5) * dx, (iy + 0.5) * dy
        d = (cx - x) ** 2 + (cy - y) ** 2
        if d < best_d:
            best_d, best = d, (cx, cy)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", help="A 导出的 scene-bundle JSON")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    grid = bundle["grid"]
    nx, ny, dx, dy = grid["nx"], grid["ny"], grid["dx"], grid["dy"]
    walkable = bundle["walkable"]
    rho0 = bundle["initial_density"]
    exits = bundle["exits"]
    exit_cells = [tuple(c) for e in exits for c in e["cells"]]

    rng = np.random.default_rng(SEED)

    # ---- 按初始密度撒点（总质量 = 人数）----
    weights = np.array(rho0, dtype=np.float64) * dx * dy
    flat = weights.ravel()
    flat = flat / flat.sum()
    total_people = int(round(weights.sum()))
    candidates_idx = rng.choice(len(flat), size=total_people, p=flat)
    ix_all = candidates_idx % nx
    iy_all = candidates_idx // nx

    # 每格内随机抖动
    xs = (ix_all + rng.random(total_people)) * dx
    ys = (iy_all + rng.random(total_people)) * dy

    # 只保留可通行格子上的点
    ok = np.array([walkable[iy][ix] for ix, iy in zip(ix_all, iy_all)], dtype=bool)
    xs, ys, ix_all, iy_all = xs[ok], ys[ok], ix_all[ok], iy_all[ok]
    n = len(xs)

    # 每人目标点：最近出口单元中心
    targets = np.zeros((n, 2))
    for i in range(n):
        targets[i] = nearest_exit_cell(exit_cells, xs[i], ys[i], dx, dy)

    speed = 1.2  # m/s
    agent_ids = [f"A{i:04d}" for i in range(n)]
    keep = np.zeros(n, dtype=bool)
    keep[::SUBSAMPLE] = True

    frames = []
    exit_events = []
    alive = np.ones(n, dtype=bool)
    t = 0.0
    while t <= HORIZON + 1e-9:
        dirs = targets - np.stack([xs, ys], axis=1)
        dist = np.linalg.norm(dirs, axis=1)
        dist[dist < 1e-9] = 1e-9
        vx = dirs[:, 0] / dist * speed
        vy = dirs[:, 1] / dist * speed

        agents = []
        for i in np.nonzero(alive & keep)[0]:
            agents.append({
                "agent_id": agent_ids[i],
                "position_m": [round(float(xs[i]), 4), round(float(ys[i]), 4)],
                "velocity_mps": [round(float(vx[i]), 4), round(float(vy[i]), 4)],
            })
        frames.append({"time_s": round(t, 3), "agents": agents})

        # 步进
        xs = xs + vx * DT
        ys = ys + vy * DT
        t += DT

        # 到达出口
        reached = dist <= speed * DT + 1e-6
        for i in np.nonzero(reached & alive)[0]:
            nearest = None
            best = float("inf")
            for e in exits:
                for (ex_i, ex_j) in e["cells"]:
                    cx, cy = (ex_i + 0.5) * dx, (ex_j + 0.5) * dy
                    d = (cx - xs[i]) ** 2 + (cy - ys[i]) ** 2
                    if d < best:
                        best, nearest = d, e["exit_id"]
            if nearest:
                exit_events.append({
                    "agent_id": agent_ids[i],
                    "exit_id": nearest,
                    "time_s": round(t, 3),
                })
                alive[i] = False

        if not alive.any():
            # 全部离开后补一帧空帧，保证观测区间完整
            frames.append({"time_s": round(t, 3), "agents": []})
            break

    # ---- analysis：D 的口径（cell 方法，previous_sample 保持）----
    # 密度统计用聚合格：A 的栅格是 0.5 m，单格面积仅 0.25 m²，
    # 采样后单格 1 人就会算出 4 人/m²，再乘采样率会虚高到 24+。
    # 用 DENSITY_AGG 个栅格合成一个统计格（2 m），这是密度热力图的常见做法。
    DENSITY_AGG = 4
    stat_nx = (nx + DENSITY_AGG - 1) // DENSITY_AGG
    stat_ny = (ny + DENSITY_AGG - 1) // DENSITY_AGG
    stat_area = (dx * DENSITY_AGG) ** 2
    times = [f["time_s"] for f in frames]
    total = total_people
    active_ids = set(agent_ids)
    exited_ids = set()
    remaining = []
    congested_area = []
    exit_cum = {e["exit_id"]: [] for e in exits}
    hotspot_map = {}   # (ix,iy) -> [start, end, peak]

    for f in frames:
        present = [a for a in f["agents"]]
        remaining.append(len(present))
        # 密度：在聚合格上统计采样人数，再按采样率还原
        dens = np.zeros((stat_ny, stat_nx))
        for a in present:
            ix = int(a["position_m"][0] / dx) // DENSITY_AGG
            iy = int(a["position_m"][1] / dy) // DENSITY_AGG
            if 0 <= ix < stat_nx and 0 <= iy < stat_ny:
                dens[iy][ix] += 1.0
        dens = dens * SUBSAMPLE / stat_area
        area = float((dens > THRESHOLD).sum()) * stat_area
        congested_area.append(area)
        for (iy, ix) in zip(*np.nonzero(dens > THRESHOLD)):
            key = (int(ix), int(iy))
            peak = float(dens[iy][ix])
            if key not in hotspot_map:
                hotspot_map[key] = {"start": f["time_s"], "end": f["time_s"], "peak": peak}
            else:
                hotspot_map[key]["end"] = f["time_s"]
                hotspot_map[key]["peak"] = max(hotspot_map[key]["peak"], peak)
        for e in exits:
            cnt = sum(1 for ev in exit_events if ev["exit_id"] == e["exit_id"] and ev["time_s"] <= f["time_s"])
            exit_cum[e["exit_id"]].append(cnt)

    congestion_area_time = float(sum(
        congested_area[k] * (times[k + 1] - times[k]) for k in range(len(times) - 1)))

    analysis = {
        "schema_version": "0.3.0",
        "scene_id": bundle["scene_id"],
        "scene_revision": bundle["scene_revision"],
        "input_hash": bundle["provenance"]["input_hash"],
        "source": "mock",
        "derived_from": "demo-trajectory-gym",
        "initial_state_hash": sha256_hex("demo-initial-state"),
        "model_sha256": None,
        "simulation_settings": {
            "dt_s": DT, "seed": SEED,
            "integrator": "straight-line-to-nearest-exit-not-physical",
            "planner_version": "demo-1", "collision_version": "none-demo",
        },
        "reproducibility_record": "tools/make_demo_trajectory.py（合成演示数据，非模型输出）",
        "density_estimator": {
            "method": "cell", "bandwidth_m": None, "support_sigma": None,
            "boundary": "whole_walkable_cell", "version": "1",
        },
        "analysis_id": "demo-gym-analysis",
        "status": "normal",
        "method_version": "demo-analysis-0.1",
        "time_range_s": [times[0], times[-1]],
        "grid": grid,
        "threshold": {
            "value": THRESHOLD, "unit": "person/m2", "comparison": ">",
            "basis": "演示阈值，非安全标准；正式阈值由 D 在 BOOT-06 确认",
        },
        "metrics": {
            "remaining_at_end": {
                "value": remaining[-1], "unit": "person",
                "definition": "最后记录时刻仍在场的人数（演示数据，按 SUBSAMPLE 未还原）",
                "source": "mock", "status": "normal",
            },
            "congestion_area_time": {
                "value": round(congestion_area_time, 2), "unit": "m2*s",
                "definition": "超过阈值的面积按 previous_sample 积分",
                "source": "mock", "status": "normal",
            },
            "exited_people": {
                "value": len(exit_events), "unit": "person",
                "definition": "记录区间内唯一的出口退出事件数（采样人数）",
                "source": "mock", "status": "normal",
            },
        },
        "series": [
            {"metric_id": "remaining_people", "time_s": times, "values": remaining,
             "unit": "person", "source": "mock", "status": "normal",
             "interpolation": "previous_sample"},
            {"metric_id": "congested_area", "time_s": times,
             "values": [round(v, 2) for v in congested_area],
             "unit": "m2", "source": "mock", "status": "normal",
             "interpolation": "previous_sample"},
        ] + [
            {"metric_id": f"exit_cumulative:{e['exit_id']}", "time_s": times,
             "values": exit_cum[e["exit_id"]], "unit": "person", "source": "mock",
             "status": "normal", "interpolation": "previous_sample"}
            for e in exits
        ],
        "hotspots": [
            {
                # 热点坐标转回原始栅格索引（热力图和 2D 视图用原始网格定位），
                # 取聚合格的左下角栅格作为代表，并在定义里说明聚合尺度
                "ix": ix * DENSITY_AGG,
                "iy": iy * DENSITY_AGG,
                "start_time_s": round(v["start"], 3),
                "end_time_s": round(v["end"], 3),
                "observed_duration_s": round(v["end"] - v["start"], 3),
                "peak_density": round(v["peak"], 3),
                "unit": "person/m2", "source": "mock", "status": "normal",
            }
            # 只保留持续 >= 1 s 的热点，按峰值排序取前 80 个
            for (ix, iy), v in sorted(
                ((k, v) for k, v in hotspot_map.items() if v["end"] - v["start"] >= 1.0),
                key=lambda kv: -kv[1]["peak"],
            )[:80]
        ],
    }

    trajectory = {
        "schema_version": "0.2.0",
        "result_id": "demo-trajectory-gym",
        "scene_id": bundle["scene_id"],
        "scene_revision": bundle["scene_revision"],
        "input_hash": bundle["provenance"]["input_hash"],
        "initial_state_hash": sha256_hex("demo-initial-state"),
        "source": "mock",
        "population_mode": "fixed_closed",
        "model_sha256": None,
        "reproducibility_record": "tools/make_demo_trajectory.py（合成演示，非模型输出）",
        "simulation_settings": {
            "dt_s": DT, "seed": SEED,
            "integrator": "straight-line-to-nearest-exit-not-physical",
            "planner_version": "demo-1", "collision_version": "none-demo",
        },
        "grid": grid,
        "walkable": walkable,
        "exit_ids": [e["exit_id"] for e in exits],
        "frames": frames,
        "exit_events": exit_events,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(trajectory, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    out_a = out.with_name(out.name.replace(".trajectory.", ".analysis."))
    out_a.write_text(json.dumps(analysis, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"轨迹: {out}  ({out.stat().st_size/1024:.0f} KB)")
    print(f"  原始人数 {total_people}，轨迹采样 {int(keep.sum())} 人，{len(frames)} 帧，"
          f"{times[-1]:.1f}s，退出事件 {len(exit_events)}")
    print(f"分析: {out_a}  ({out_a.stat().st_size/1024:.0f} KB)")
    print(f"  ⚠ 这是合成演示数据（source=mock），不是模型输出，不代表真实疏散效果")
    return 0


if __name__ == "__main__":
    sys.exit(main())