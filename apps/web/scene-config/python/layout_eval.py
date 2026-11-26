# -*- coding: utf-8 -*-
"""A 模块候选布局接口：把 D 的设施位姿修改变成可校验的新场景与去重报告。

职责边界：
    A：只接受“可移动/可改尺寸”的参数化设施位姿修改，生成新场景并做几何校验；
    D：生成候选、按报告过滤不可行/重复布局，再交给 C 跑 PDE；
    C：读取 rasterize/export_npz 的 walkable、rho0、掩码和哈希。

命令行：
    python layout_eval.py <scene.json> <candidates.json> [--out report.json]
                          [--write-scene first_feasible.json]
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scene_io import (
    FORMAT_VERSION,
    _facilities,
    _scene_revision,
    _variable_parameter_problems,
    facility_poses,
    load_scene,
    rasterize,
    refresh_variable_parameters,
    validate,
    variable_parameter_value,
    variable_parameters,
)


class LayoutError(ValueError):
    """候选修改本身不合法（不是几何不可行，而是不允许改/字段错误）。"""


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _angle_delta(a: float, b: float) -> float:
    return abs((float(b) - float(a) + 180.0) % 360.0 - 180.0)


def apply_layout(scene: dict, changes: list[dict]) -> dict:
    """在场景副本上应用设施位姿修改，返回新场景（不修改入参）。

    只允许修改 facilities 中标记为 movable / resizable 的字段：
        center, rotation_deg（需要 movable=true）
        length, width（需要 resizable=true 且在 size_limits 内）
    以及 exits 中的 width；声明过的可变参数范围也会作为硬约束。
    """
    if scene.get("version") != FORMAT_VERSION:
        raise LayoutError(f"设施布局修改需要场景版本 {FORMAT_VERSION}；"
                          f"当前为 {scene.get('version')}（v0.1 只有固定障碍）")
    if not isinstance(changes, list) or not changes:
        raise LayoutError("候选修改必须是非空数组")

    new = copy.deepcopy(scene)
    facilities = _facilities(new)
    by_id = {f["id"]: f for f in facilities}
    exits_by_id = {ex["id"]: ex for ex in new.get("exits", [])}
    seen = set()

    for change in changes:
        if not isinstance(change, dict):
            raise LayoutError("每条修改必须是对象")
        has_facility = "facility_id" in change
        has_exit = "exit_id" in change
        if has_facility == has_exit:
            raise LayoutError("每条修改必须且只能提供 facility_id 或 exit_id")
        if has_exit:
            eid = change.get("exit_id")
            if not isinstance(eid, str) or not eid:
                raise LayoutError("exit_id 必须是非空字符串")
            key = f"exit:{eid}"
            if key in seen:
                raise LayoutError(f"同一候选重复修改出口 {eid}")
            seen.add(key)
            if eid not in exits_by_id:
                raise LayoutError(f"未知出口 id：{eid}")
            unknown = set(change) - {"exit_id", "width"}
            if unknown:
                raise LayoutError(f"出口 {eid} 含不允许修改的字段：{sorted(unknown)}")
            if "width" not in change or not _finite(change["width"]) or float(change["width"]) <= 0:
                raise LayoutError(f"出口 {eid} 的 width 必须为正数")
            exits_by_id[eid]["width"] = float(change["width"])
            continue
        fid = change.get("facility_id")
        if not isinstance(fid, str) or not fid:
            raise LayoutError("facility_id 必须是非空字符串")
        key = f"facility:{fid}"
        if key in seen:
            raise LayoutError(f"同一候选重复修改设施 {fid}")
        seen.add(key)
        if fid not in by_id:
            raise LayoutError(f"未知设施 id：{fid}（固定障碍不可移动）")

        f = by_id[fid]
        unknown = set(change) - {"facility_id", "center", "rotation_deg", "length", "width"}
        if unknown:
            raise LayoutError(f"设施 {fid} 含不允许修改的字段：{sorted(unknown)}")
        moving = "center" in change or "rotation_deg" in change
        resizing = "length" in change or "width" in change
        if not moving and not resizing:
            raise LayoutError(f"设施 {fid} 的修改没有任何几何字段")
        if moving and not f.get("movable", False):
            raise LayoutError(f"设施 {fid} 不允许移动（movable=false）")
        if resizing and not f.get("resizable", False):
            raise LayoutError(f"设施 {fid} 不允许改尺寸（resizable=false）")

        if "center" in change:
            center = change["center"]
            if not isinstance(center, (list, tuple)) or len(center) != 2 or \
                    not all(_finite(v) for v in center):
                raise LayoutError(f"设施 {fid} 的 center 必须是两个有限数 [x, y]")
            f["center"] = [float(center[0]), float(center[1])]
        if "rotation_deg" in change:
            rot = change["rotation_deg"]
            if not _finite(rot):
                raise LayoutError(f"设施 {fid} 的 rotation_deg 必须是有限数")
            f["rotation_deg"] = float(rot) % 360.0
        for key in ("length", "width"):
            if key in change:
                value = change[key]
                if not _finite(value) or float(value) <= 0:
                    raise LayoutError(f"设施 {fid} 的 {key} 必须为正数")
                f[key] = float(value)

        if f.get("resizable", False):
            limits = f.get("size_limits") or {}
            for key in ("length", "width"):
                lim = limits.get(key)
                if lim and not (float(lim[0]) - 1e-9 <= float(f[key]) <= float(lim[1]) + 1e-9):
                    raise LayoutError(
                        f"设施 {fid} 的 {key}={f[key]} 超出 size_limits {lim}")

    refresh_variable_parameters(new)
    declaration_problems = _variable_parameter_problems(new)
    if declaration_problems:
        raise LayoutError(declaration_problems[0])

    old_rev = _scene_revision(new)
    new.setdefault("meta", {})
    new["meta"]["revision"] = old_rev + 1
    new["meta"]["parent_revision"] = old_rev
    return new


def _pose_delta(scene: dict, new_scene: dict, changes: list[dict]) -> dict:
    old_by_id = {f["id"]: f for f in _facilities(scene)}
    new_by_id = {f["id"]: f for f in _facilities(new_scene)}
    items = []
    total_move = 0.0
    total_rot = 0.0
    modified = False
    for change in changes:
        if "facility_id" not in change:
            continue
        fid = change["facility_id"]
        old, new = old_by_id[fid], new_by_id[fid]
        move = math.hypot(float(new["center"][0]) - float(old["center"][0]),
                          float(new["center"][1]) - float(old["center"][1]))
        rot = _angle_delta(old["rotation_deg"], new["rotation_deg"])
        dl = float(new["length"]) - float(old["length"])
        dw = float(new["width"]) - float(old["width"])
        total_move += move
        total_rot += rot
        modified = modified or move > 1e-12 or rot > 1e-12 or abs(dl) > 1e-12 or abs(dw) > 1e-12
        items.append({
            "facility_id": fid,
            "displacement_m": round(move, 6),
            "rotation_delta_deg": round(rot, 6),
            "size_delta": [round(dl, 6), round(dw, 6)],
        })
    return {
        "items": items,
        "move_distance_m": round(total_move, 6),
        "rotation_delta_deg": round(total_rot, 6),
        "modified": modified,
    }


def _exit_width_deltas(scene: dict, new_scene: dict, changes: list[dict]) -> list[dict]:
    old_by_id = {ex["id"]: ex for ex in scene.get("exits", [])}
    new_by_id = {ex["id"]: ex for ex in new_scene.get("exits", [])}
    items = []
    for change in changes:
        if "exit_id" not in change:
            continue
        eid = change["exit_id"]
        before = float(old_by_id[eid]["width"])
        after = float(new_by_id[eid]["width"])
        items.append({
            "exit_id": eid,
            "before_width_m": round(before, 6),
            "after_width_m": round(after, 6),
            "width_delta_m": round(after - before, 6),
        })
    return items


def _variable_parameter_deltas(scene: dict, new_scene: dict) -> list[dict]:
    before_by_binding = {
        (item["type"], item["target_id"]): item
        for item in variable_parameters(scene)
    }
    items = []
    for after in variable_parameters(new_scene):
        before = before_by_binding[(after["type"], after["target_id"])]
        before_value = variable_parameter_value(scene, before)
        after_value = variable_parameter_value(new_scene, after)
        item = {
            "name": after["name"],
            "type": after["type"],
            "target_id": after["target_id"],
            "unit": after["unit"],
            "allowed_range": copy.deepcopy(after["allowed_range"]),
            "before_value": round(before_value, 6),
            "after_value": round(after_value, 6),
            "delta": round(after_value - before_value, 6),
            "changed": abs(after_value - before_value) > 1e-12,
        }
        if "scene_description" in after:
            item["scene_description"] = after["scene_description"]
        if "scene_diff" in after:
            item["scene_diff"] = copy.deepcopy(after["scene_diff"])
        items.append(item)
    return items


def _failed(candidate_id: str | None, message: str, changes: list[dict] | None = None,
            declarations: list[dict] | None = None) -> dict:
    return {
        "candidate_id": candidate_id,
        "changes": changes or [],
        "feasible": False,
        "problems": [{"level": "error", "msg": message}],
        "scene_revision": None,
        "layout": [],
        "changed_facilities": [],
        "changed_exits": [],
        "pose_delta": [],
        "exit_width_deltas": [],
        "move_distance_m": 0.0,
        "rotation_delta_deg": 0.0,
        "variable_parameters": copy.deepcopy(declarations or []),
        "variable_parameter_deltas": [],
        "mask_changed": False,
        "below_grid_resolution": False,
        "mask_hash": None,
        "walkable_hash": None,
        "input_hash": None,
        "layout_hash": None,
        "metrics": {},
        "solve_required": False,
        "duplicate_of": None,
        "duplicate_mask_of": None,
    }


def evaluate_layout(scene: dict, changes: list[dict], base_raster: dict | None = None,
                    candidate_id: str | None = None) -> dict:
    """应用候选并返回几何可行性、掩码和修改量，不抛出几何不可行异常。"""
    base_R = base_raster if base_raster is not None else rasterize(scene)
    try:
        new_scene = apply_layout(scene, changes)
    except LayoutError as exc:
        return _failed(candidate_id, str(exc), changes, variable_parameters(scene))

    declarations = variable_parameters(new_scene)
    parameter_deltas = _variable_parameter_deltas(scene, new_scene)
    exit_deltas = _exit_width_deltas(scene, new_scene, changes)
    R = rasterize(new_scene)
    problems = validate(new_scene, R)
    feasible = not any(p["level"] == "error" for p in problems)
    delta = _pose_delta(scene, new_scene, changes)
    mask_changed = R["mask_hash"] != base_R["mask_hash"]
    below_grid = bool(delta["modified"] and not mask_changed)
    if below_grid:
        problems.append({
            "level": "warning",
            "msg": f"修改后几何不同，但栅格掩码未变化（位移小于约 {R['res']} 米）；"
                   f"D 应跳过该候选的重复 PDE 求解",
        })

    return {
        "candidate_id": candidate_id,
        "changes": changes,
        "feasible": feasible,
        "problems": problems,
        "scene_revision": _scene_revision(new_scene),
        "layout": facility_poses(new_scene),
        "changed_facilities": [c["facility_id"] for c in changes if "facility_id" in c],
        "changed_exits": [c["exit_id"] for c in changes if "exit_id" in c],
        "pose_delta": delta["items"],
        "exit_width_deltas": exit_deltas,
        "move_distance_m": delta["move_distance_m"],
        "rotation_delta_deg": delta["rotation_delta_deg"],
        "variable_parameters": copy.deepcopy(declarations),
        "variable_parameter_deltas": parameter_deltas,
        "mask_changed": mask_changed,
        "below_grid_resolution": below_grid,
        "mask_hash": R["mask_hash"],
        "walkable_hash": R["walkable_hash"],
        "input_hash": R["input_hash"],
        "layout_hash": R["layout_hash"],
        "metrics": {
            "walkable_cells": int(R["walkable"].sum()),
            "obstacle_cells": int(R["obstacle_mask"].sum()),
            "facility_cells": int(R["facility_mask"].sum()),
            "crowd_covered_cells": int(sum(v["cells"] for v in R["crowd_covered_by_zone"].values())),
            "exit_count": len(R["exit_cells"]),
            "grid_cells": int(R["walkable"].size),
        },
        "solve_required": bool(feasible),
        "duplicate_of": None,
        "duplicate_mask_of": None,
    }


def evaluate_candidates(scene: dict, candidates: list[dict]) -> dict:
    """批量评价并去重。solve_required=False 的候选不需要 C 重复求解。"""
    if scene.get("version") != FORMAT_VERSION:
        raise LayoutError(f"候选评价需要场景版本 {FORMAT_VERSION}；当前为 {scene.get('version')}")
    if not isinstance(candidates, list):
        raise LayoutError("candidates 必须是数组")

    base_R = rasterize(scene)
    declarations = variable_parameters(scene)
    reports = []
    seen_inputs = {base_R["input_hash"]: "baseline"}
    seen_masks = {base_R["mask_hash"]: "baseline"}

    for idx, cand in enumerate(candidates):
        if not isinstance(cand, dict):
            reports.append(_failed(f"C{idx + 1}", "候选必须是对象", declarations=declarations))
            continue
        cid = cand.get("candidate_id") or cand.get("id") or f"C{idx + 1}"
        changes = cand.get("changes")
        if not isinstance(changes, list):
            reports.append(_failed(cid, "候选缺少 changes 数组", declarations=declarations))
            continue
        report = evaluate_layout(scene, changes, base_raster=base_R, candidate_id=cid)
        if report["feasible"]:
            h = report["input_hash"]
            if h in seen_inputs:
                report["duplicate_of"] = seen_inputs[h]
                report["solve_required"] = False
            else:
                seen_inputs[h] = cid
                report["solve_required"] = True
            mh = report["mask_hash"]
            if mh in seen_masks:
                report["duplicate_mask_of"] = seen_masks[mh]
            else:
                seen_masks[mh] = cid
        else:
            report["solve_required"] = False
        reports.append(report)

    feasible = [r for r in reports if r["feasible"]]
    unique = [r for r in feasible if r["solve_required"]]
    return {
        "scene_name": scene.get("meta", {}).get("name"),
        "scene_version": scene.get("version"),
        "base_revision": _scene_revision(scene),
        "base_layout_hash": base_R["layout_hash"],
        "base_mask_hash": base_R["mask_hash"],
        "base_walkable_hash": base_R["walkable_hash"],
        "base_input_hash": base_R["input_hash"],
        "variable_parameters": copy.deepcopy(declarations),
        "candidate_count": len(reports),
        "feasible_count": len(feasible),
        "unique_solve_count": len(unique),
        "duplicates_skipped": len(feasible) - len(unique),
        "candidates": reports,
    }


def load_candidates(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("candidates"), list):
        return data["candidates"]
    raise ValueError("候选文件应为数组，或包含 candidates 数组的对象")


def main() -> int:
    ap = argparse.ArgumentParser(description="A 模块候选布局评价：几何校验 + 掩码去重")
    ap.add_argument("scene", help="v0.2 场景 JSON")
    ap.add_argument("candidates", help="候选 JSON（数组或 {candidates:[...]}）")
    ap.add_argument("--out", default=None, help="输出报告 JSON")
    ap.add_argument("--write-scene", default=None,
                    help="把第一个需要求解的可行候选写成新场景 JSON")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    try:
        scene = load_scene(args.scene)
        candidates = load_candidates(args.candidates)
        report = evaluate_candidates(scene, candidates)
    except (ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    print(f"场景『{report['scene_name']}』 rev {report['base_revision']}  "
          f"基准 mask={report['base_mask_hash'][:12]}")
    for r in report["candidates"]:
        status = "可行" if r["feasible"] else "不可行"
        extra = ""
        if r["duplicate_of"]:
            extra = f"  重复于 {r['duplicate_of']}（跳过求解）"
        elif r["feasible"]:
            extra = "  需要求解" if r["solve_required"] else ""
        print(f"  {r['candidate_id']}: {status}  mask={str(r['mask_hash'])[:12]}  "
              f"move={r['move_distance_m']:.2f}m  {extra}")
        for p in r["problems"]:
            if p["level"] == "error":
                print(f"      [ERROR] {p['msg']}")
    print(f"候选 {report['candidate_count']} 个：可行 {report['feasible_count']}，"
          f"需真实求解 {report['unique_solve_count']}，跳过重复 {report['duplicates_skipped']}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告: {out}")

    if args.write_scene:
        picked = next((r for r in report["candidates"] if r["solve_required"]), None)
        if picked is None:
            print("没有需要求解的可行候选，未写场景。")
        else:
            new_scene = apply_layout(scene, picked["changes"])
            out = Path(args.write_scene)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(new_scene, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已写新场景: {out}（候选 {picked['candidate_id']}）")

    return 0 if report["feasible_count"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
