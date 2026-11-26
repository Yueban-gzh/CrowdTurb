# -*- coding: utf-8 -*-
"""ArchFlow AI —— A 模块（初始场配置）核心库：场景加载 / 校验 / 栅格化。

数据流：
    编辑器保存的 scenes/*.json（v0.1）
    或含参数化设施的 scenes/*.json（v0.2）
        └─ load_scene()      读入并做结构校验
        └─ rasterize()       固定障碍 + 参数化设施 + 人群 → 初始密度场 rho0 / 可行走栅格
        └─ validate()        几何校验（越界、重叠、压人群、堵出口、切断通路、窄通道等）
        └─ export_npz()      导出 C 可直接使用的 .npz（含掩码与哈希）

坐标与数组约定（组内统一，勿改）：
    - 世界坐标：米；原点在场地左下角，x 向右（长 L），y 向上（宽 H）。
    - 栅格：Nx = round(L/res), Ny = round(H/res)；数组形状 (Ny, Nx)。
    - rho0[j, i] 对应格子中心 (x=(i+0.5)*res, y=(j+0.5)*res)；j=0 是南边（y=0）。
    - 密度单位：人/平方米（p/m²）。rho0 的总质量 * res² = 场内总人数。
    - walkable: bool (Ny, Nx)，True=可通行；最外圈默认是墙，出口处开口。
    - 设施栅格化规则：格心落在旋转矩形内（含边界）即不可通行。移动小于
      resolution 时可能产生相同掩码，由 mask_hash/input_hash 识别，调用方应去重。
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import sys
from collections import deque
from pathlib import Path

import numpy as np

from passages import check_passage, passage_banks, passage_envelope, passage_intersects_wall

from geometry import (
    EPS,
    convex_polygons_overlap,
    facility_polygon,
    polygon_area,
    polygon_inside_polygon,
    polygon_inside_rect,
    polygon_mask,
    rect_polygon,
    rotated_rect_mask,
    segment_intersects_polygon_interior,
    segment_polygon_distance,
)

FORMAT_VERSION = "0.3"
SUPPORTED_VERSIONS = ("0.1", "0.2", "0.3")
SIDES = ("N", "S", "E", "W")
WALL_MARGIN = 0.3      # 出口开口离墙角的最小距离（米），避免贴角开门
DEFAULT_JAM = 4.0      # 默认拥堵密度阈值（人/m²），超过只告警
DEFAULT_MIN_PASSAGE = 0.9   # 默认最小必要通路宽度（米），0=不检查
DEFAULT_EXIT_CLEARANCE = 0.5  # 设施到出口开口的最小距离（米）
PROFILE_VALUES = ("seated", "standing")
VARIABLE_PARAMETER_TYPES = {
    "passage_width": {"target": "passage", "field": "width", "unit": "m"},
    "exit_width": {"target": "exit", "field": "width", "unit": "m"},
    "facility_center_x": {"target": "facility", "field": "center_x", "unit": "m"},
    "facility_center_y": {"target": "facility", "field": "center_y", "unit": "m"},
    "facility_rotation": {"target": "facility", "field": "rotation_deg", "unit": "deg"},
    "facility_length": {"target": "facility", "field": "length", "unit": "m"},
    "facility_width": {"target": "facility", "field": "width", "unit": "m"},
}


# ---------------------------------------------------------------- 基础工具

def _err(level: str, msg: str) -> dict:
    return {"level": level, "msg": msg}          # level: "error" / "warning"


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and \
        math.isfinite(float(value))


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_parts(parts, meta=None) -> str:
    h = hashlib.sha256()
    if meta is not None:
        h.update(_canonical_json(meta).encode("utf-8"))
        h.update(b"\0")
    for label, arr in parts:
        a = np.ascontiguousarray(arr)
        h.update(label.encode("utf-8"))
        h.update(b"\0")
        h.update(a.dtype.str.encode("ascii"))
        h.update(str(a.shape).encode("ascii"))
        h.update(a.tobytes())
    return h.hexdigest()


def _poly_mask(XX: np.ndarray, YY: np.ndarray, poly) -> np.ndarray:
    return polygon_mask(XX, YY, poly)


def exit_segment(ex: dict, size) -> tuple[float, float, float, float]:
    """把出口（side+offset+width）换算成世界坐标线段 [x1,y1,x2,y2]。"""
    L, H = size
    s = ex["side"]
    off, w = ex["offset"], ex["width"]
    if s == "S":
        return (off, 0.0, off + w, 0.0)
    if s == "N":
        return (off, H, off + w, H)
    if s == "W":
        return (0.0, off, 0.0, off + w)
    if s == "E":
        return (L, off, L, off + w)
    raise ValueError(f"未知出口方向: {s}")


def _facilities(scene: dict) -> list[dict]:
    return scene.get("geometry", {}).get("facilities", []) or []


def _passages(scene: dict) -> list[dict]:
    return scene.get("geometry", {}).get("passages", [])


def _layout_policy(scene: dict) -> dict:
    raw = scene.get("geometry", {}).get("layout_policy", {}) or {}
    return {
        "min_passage_width": float(raw.get("min_passage_width", DEFAULT_MIN_PASSAGE)),
        "exit_clearance": float(raw.get("exit_clearance", DEFAULT_EXIT_CLEARANCE)),
        "cell_rule": "center",
    }


def facility_poses(scene: dict) -> list[dict]:
    """按 id 排序的设施位姿，用于布局签名与报告。"""
    poses = []
    for f in sorted(_facilities(scene), key=lambda item: item["id"]):
        poses.append({
            "id": f["id"],
            "center": [round(float(f["center"][0]), 6), round(float(f["center"][1]), 6)],
            "length": round(float(f["length"]), 6),
            "width": round(float(f["width"]), 6),
            "rotation_deg": round(float(f["rotation_deg"]) % 360.0, 6),
        })
    return poses


def _scene_revision(scene: dict) -> int:
    return int(scene.get("meta", {}).get("revision", 1))


def variable_parameters(scene: dict) -> list[dict]:
    return scene.get("variable_parameters", [])


def _variable_target(scene: dict, declaration: dict) -> dict:
    spec = VARIABLE_PARAMETER_TYPES.get(declaration.get("type"))
    if spec is None:
        raise ValueError(f"不支持的可变参数类型: {declaration.get('type')}")
    targets = {"exit": scene["exits"], "facility": _facilities(scene),
               "passage": _passages(scene)}[spec["target"]]
    target_id = declaration.get("target_id")
    for target in targets:
        if target.get("id") == target_id:
            return target
    raise ValueError(f"可变参数 {declaration.get('name')} 引用了未知对象: {target_id}")


def variable_parameter_value(scene: dict, declaration: dict) -> float:
    target = _variable_target(scene, declaration)
    field = VARIABLE_PARAMETER_TYPES[declaration["type"]]["field"]
    if field == "center_x":
        return float(target["center"][0])
    if field == "center_y":
        return float(target["center"][1])
    if field == "rotation_deg":
        return float(target["rotation_deg"]) % 360.0
    return float(target[field])


def _variable_parameter_change(scene: dict, declaration: dict) -> dict:
    target = _variable_target(scene, declaration)
    field = VARIABLE_PARAMETER_TYPES[declaration["type"]]["field"]
    if declaration["type"] == "exit_width":
        return {"exit_id": target["id"], "width": float(target["width"])}
    if declaration["type"] == "passage_width":
        return {"passage_id": target["id"], "width": float(target["width"])}
    if field in ("center_x", "center_y"):
        return {"facility_id": target["id"],
                "center": [float(target["center"][0]), float(target["center"][1])]}
    return {"facility_id": target["id"], field: float(target[field])}


def refresh_variable_parameters(scene: dict) -> None:
    declarations = variable_parameters(scene)
    if not isinstance(declarations, list):
        return
    for declaration in declarations:
        if not isinstance(declaration, dict):
            continue
        try:
            declaration["current_value"] = variable_parameter_value(scene, declaration)
            if "scene_diff" in declaration:
                declaration["scene_diff"] = [_variable_parameter_change(scene, declaration)]
        except (KeyError, TypeError, ValueError):
            continue


def _variable_parameter_problems(scene: dict) -> list[str]:
    raw = variable_parameters(scene)
    if not isinstance(raw, list):
        return ["variable_parameters 必须是数组"]
    problems = []
    seen_names = set()
    seen_bindings = set()
    required = {"name", "type", "target_id", "unit", "allowed_range", "current_value"}
    allowed = required | {"scene_description", "scene_diff"}
    for index, declaration in enumerate(raw):
        if not isinstance(declaration, dict):
            problems.append(f"可变参数 #{index} 必须是对象")
            continue
        name = declaration.get("name") or f"#{index}"
        label = f"可变参数『{name}』"
        missing = required - set(declaration)
        if missing:
            problems.append(f"{label} 缺少字段: {sorted(missing)}")
            continue
        unknown = set(declaration) - allowed
        if unknown:
            problems.append(f"{label} 含未知字段: {sorted(unknown)}")
        if not isinstance(declaration["name"], str) or not declaration["name"].strip():
            problems.append(f"{label} 的 name 必须是非空字符串")
        elif declaration["name"] in seen_names:
            problems.append(f"{label} 的 name 重复")
        else:
            seen_names.add(declaration["name"])
        has_description = "scene_description" in declaration
        has_diff = "scene_diff" in declaration
        if has_description == has_diff:
            problems.append(f"{label} 必须且只能提供 scene_description 或 scene_diff 之一")
        if has_description and (not isinstance(declaration["scene_description"], str) or
                                 not declaration["scene_description"].strip()):
            problems.append(f"{label} 的 scene_description 必须是非空字符串")

        ptype = declaration["type"]
        if not isinstance(ptype, str):
            problems.append(f"{label} 的 type 必须是非空字符串")
            continue
        spec = VARIABLE_PARAMETER_TYPES.get(ptype)
        if spec is None:
            problems.append(f"{label} 的 type 不支持: {ptype}")
            continue
        if declaration["unit"] != spec["unit"]:
            problems.append(f"{label} 的 unit 应为 {spec['unit']}")
        target_id = declaration["target_id"]
        if not isinstance(target_id, str) or not target_id:
            problems.append(f"{label} 的 target_id 必须是非空字符串")
            continue
        binding = (ptype, target_id)
        if binding in seen_bindings:
            problems.append(f"{label} 与同一对象同一参数的声明重复")
        seen_bindings.add(binding)

        limits = declaration["allowed_range"]
        if not isinstance(limits, (list, tuple)) or len(limits) != 2 or \
                not all(_finite(v) for v in limits) or float(limits[0]) > float(limits[1]):
            problems.append(f"{label} 的 allowed_range 必须是 [min, max] 且 min<=max")
            continue
        if ptype in ("exit_width", "passage_width", "facility_length", "facility_width") and float(limits[0]) <= 0:
            problems.append(f"{label} 的 allowed_range 最小值必须为正数")
        if ptype == "facility_rotation" and (float(limits[0]) < 0 or float(limits[1]) > 360):
            problems.append(f"{label} 的 allowed_range 应在 [0, 360] 度内")
        current = declaration["current_value"]
        if not _finite(current):
            problems.append(f"{label} 的 current_value 必须是有限数")
            continue
        if not (float(limits[0]) - EPS <= float(current) <= float(limits[1]) + EPS):
            problems.append(f"{label} 的 current_value 超出 allowed_range")

        try:
            target = _variable_target(scene, declaration)
            actual = variable_parameter_value(scene, declaration)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if abs(actual - float(current)) > EPS:
            problems.append(f"{label} 的 current_value 与场景实际值 {actual} 不一致")
        if spec["target"] == "passage":
            if not target["adjustable"]:
                problems.append(f"{label} 引用的通道不允许调整")
            if limits[0] < target["width_limits"][0] or limits[1] > target["width_limits"][1]:
                problems.append(f"{label} 的范围超出通道 width_limits")
        if spec["target"] == "facility":
            if ptype in ("facility_center_x", "facility_center_y", "facility_rotation") and \
                    not target.get("movable", False):
                problems.append(f"{label} 引用的设施不允许移动/旋转")
            if ptype in ("facility_length", "facility_width") and not target.get("resizable", False):
                problems.append(f"{label} 引用的设施不允许改尺寸")
        if has_diff:
            diff = declaration["scene_diff"]
            if not isinstance(diff, list) or len(diff) != 1 or not isinstance(diff[0], dict):
                problems.append(f"{label} 的 scene_diff 必须恰好包含一个修改对象")
            else:
                expected = _variable_parameter_change(scene, declaration)
                if diff[0] != expected:
                    problems.append(f"{label} 的 scene_diff 与 target_id/current_value 不一致")
    return problems


# ---------------------------------------------------------------- 加载与结构校验

def _check_facility(f: dict, index: int) -> None:
    if not isinstance(f, dict):
        raise ValueError(f"设施 #{index} 必须是对象")
    label = f.get("id") or f"#{index}"
    for key in ("id", "name", "center", "length", "width", "rotation_deg", "movable", "resizable"):
        if key not in f:
            raise ValueError(f"设施 {label} 缺少字段: {key}")
    if not isinstance(f["id"], str) or not f["id"].strip():
        raise ValueError(f"设施 #{index} 的 id 必须是非空字符串")
    if not isinstance(f["name"], str):
        raise ValueError(f"设施 {label} 的 name 必须是字符串")
    center = f["center"]
    if not isinstance(center, (list, tuple)) or len(center) != 2 or \
            not all(_finite(v) for v in center):
        raise ValueError(f"设施 {label} 的 center 必须是两个有限数 [x, y]")
    for key in ("length", "width"):
        if not _finite(f[key]) or float(f[key]) <= 0:
            raise ValueError(f"设施 {label} 的 {key} 必须为正数")
    if not _finite(f["rotation_deg"]):
        raise ValueError(f"设施 {label} 的 rotation_deg 必须是有限数")
    for key in ("movable", "resizable"):
        if not isinstance(f[key], bool):
            raise ValueError(f"设施 {label} 的 {key} 必须是布尔值")
    if "effect" in f and f["effect"] != "block":
        raise ValueError(f"设施 {label} 的 effect 目前只支持 'block'（不可穿越）")
    region = f.get("allowed_region")
    if region is not None:
        if not isinstance(region, list) or len(region) < 3:
            raise ValueError(f"设施 {label} 的 allowed_region 至少需要 3 个顶点")
        for p in region:
            if not isinstance(p, (list, tuple)) or len(p) != 2 or not all(_finite(v) for v in p):
                raise ValueError(f"设施 {label} 的 allowed_region 顶点必须是 [x, y]")
        if polygon_area(region) <= EPS:
            raise ValueError(f"设施 {label} 的 allowed_region 面积必须大于 0")
    if f["resizable"]:
        limits = f.get("size_limits")
        if not isinstance(limits, dict):
            raise ValueError(f"可改尺寸的设施 {label} 必须提供 size_limits")
        for key in ("length", "width"):
            lim = limits.get(key)
            if not isinstance(lim, (list, tuple)) or len(lim) != 2 or \
                    not all(_finite(v) for v in lim) or float(lim[0]) <= 0 or \
                    float(lim[0]) > float(lim[1]):
                raise ValueError(f"设施 {label} 的 size_limits.{key} 必须是 [min, max]")
    elif "size_limits" in f:
        raise ValueError(f"设施 {label} 不可改尺寸，不应提供 size_limits")


def load_scene(path: str | Path) -> dict:
    """读取场景 JSON；结构不合格直接抛 ValueError（不猜、不静默修）。"""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        scene = json.load(f)

    version = scene.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"格式版本应为 {'/'.join(SUPPORTED_VERSIONS)}，实际为 {version}；"
                         f"接口变更需先在组内确认（分工文档 §4）。")

    for key in ("meta", "geometry", "exits", "crowd", "params"):
        if key not in scene:
            raise ValueError(f"缺少必需字段: {key}")

    if "revision" in scene.get("meta", {}) and \
            (not isinstance(scene["meta"]["revision"], int) or scene["meta"]["revision"] < 1):
        raise ValueError("meta.revision 必须是 >=1 的整数")

    geo = scene["geometry"]
    L, H = geo["size"]
    if not (L > 0 and H > 0):
        raise ValueError("geometry.size 必须为正数 [L, H]")
    if not (0.05 <= geo["resolution"] <= 2.0):
        raise ValueError("geometry.resolution 应在 0.05~2.0 米/格 之间")

    for ob in geo.get("obstacles", []):
        if not all(k in ob for k in ("name", "x", "y", "w", "h")):
            raise ValueError(f"障碍缺少字段: {ob}")
        if ob["w"] <= 0 or ob["h"] <= 0:
            raise ValueError(f"障碍 {ob['name']} 的宽高必须为正")

    if "variable_parameters" in scene and version not in ("0.2", "0.3"):
        raise ValueError("variable_parameters 需要场景版本 0.2/0.3")
    facilities = geo.get("facilities", [])
    if facilities and version not in ("0.2", "0.3"):
        raise ValueError("geometry.facilities 需要场景版本 0.2/0.3")
    if not isinstance(facilities, list):
        raise ValueError("geometry.facilities 必须是数组")
    seen_facilities = set()
    for idx, f in enumerate(facilities):
        _check_facility(f, idx)
        if f["id"] in seen_facilities:
            raise ValueError(f"设施 id 重复: {f['id']}")
        seen_facilities.add(f["id"])

    passages = _passages(scene)
    if not isinstance(passages, list):
        raise ValueError("geometry.passages 必须是数组")
    if passages and version != "0.3":
        raise ValueError("geometry.passages 需要场景版本 0.3")
    passage_ids = set()
    for passage in passages:
        check_passage(passage)
        if passage["id"] in passage_ids:
            raise ValueError(f"通道 id 重复: {passage['id']}")
        passage_ids.add(passage["id"])

    policy = geo.get("layout_policy")
    if policy is not None:
        if not isinstance(policy, dict):
            raise ValueError("geometry.layout_policy 必须是对象")
        for key in ("min_passage_width", "exit_clearance"):
            if key in policy and (not _finite(policy[key]) or float(policy[key]) < 0):
                raise ValueError(f"layout_policy.{key} 必须是非负数")
        unknown = set(policy) - {"min_passage_width", "exit_clearance"}
        if unknown:
            raise ValueError(f"layout_policy 含未知字段: {sorted(unknown)}")

    for ex in scene["exits"]:
        if not all(k in ex for k in ("id", "name", "side", "offset", "width")):
            raise ValueError(f"出口缺少字段: {ex}")
        if ex["side"] not in SIDES:
            raise ValueError(f"出口 {ex['id']} 的 side 必须是 N/S/E/W")
        if ex["width"] <= 0:
            raise ValueError(f"出口 {ex['id']} 的 width 必须为正")

    declaration_problems = _variable_parameter_problems(scene)
    if declaration_problems:
        raise ValueError(declaration_problems[0])

    for z in scene["crowd"]["zones"]:
        if not all(k in z for k in ("id", "name", "polygon", "count")):
            raise ValueError(f"人群区缺少字段: {z}")
        if len(z["polygon"]) < 3:
            raise ValueError(f"人群区 {z['id']} 的 polygon 至少 3 个顶点")
        if z["count"] <= 0:
            raise ValueError(f"人群区 {z['id']} 的 count 必须为正")
        profile = z.get("profile", "standing")
        if profile not in PROFILE_VALUES:
            raise ValueError(f"人群区 {z['id']} 的 profile 必须是 seated/standing")

    for k in ("walk_speed", "T"):
        if k not in scene["params"] or scene["params"][k] <= 0:
            raise ValueError(f"params.{k} 必须为正数")

    return scene


# ---------------------------------------------------------------- 栅格化

def rasterize(scene: dict) -> dict:
    """场景 → 栅格数据（C 的计算输入）。

    返回（除 Nx/Ny/res 外均为 (Ny, Nx)）：
        walkable            最终可行走（边界墙开口 + 固定障碍 + 设施）
        base_walkable       不含设施、只含建筑/墙/出口的可行走
        obstacle_mask       固定障碍 + 设施（不含边界墙）
        fixed_obstacle_mask 固定障碍
        facility_mask       参数化设施
        wall_mask           边界墙 + 内部隔墙（出口处已开口）
        rho0                初始密度场（人/m²），总质量=总人数
        zone_id             人群区编号（-1=无）
        crowd_mask          人群区多边形覆盖的格（不考虑是否可走）
        exit_cells          出口开口格
        exit_blocked_by_facility  被设施覆盖的出口格
        crowd_covered_by_zone     设施压到初始人群的格子/估算人数
        facility_cells_by_id      每个设施覆盖的格子数
        mask_hash / walkable_hash / input_hash / layout_hash
    """
    geo = scene["geometry"]
    L, H = geo["size"]
    res = geo["resolution"]
    Nx, Ny = int(round(L / res)), int(round(H / res))
    cx = (np.arange(Nx) + 0.5) * res          # 各格中心坐标
    cy = (np.arange(Ny) + 0.5) * res
    XX, YY = np.meshgrid(cx, cy)              # 形状 (Ny, Nx)

    # --- 固定障碍 ---
    fixed_mask = np.zeros((Ny, Nx), dtype=bool)
    for ob in geo.get("obstacles", []):
        m = (XX > ob["x"]) & (XX < ob["x"] + ob["w"]) & \
            (YY > ob["y"]) & (YY < ob["y"] + ob["h"])
        fixed_mask |= m

    # --- 内部隔墙 + 边界墙 ---
    wall_mask = np.zeros((Ny, Nx), dtype=bool)
    wall_mask[0, :] = wall_mask[-1, :] = True
    wall_mask[:, 0] = wall_mask[:, -1] = True
    for (x1, y1, x2, y2) in geo.get("walls", []):
        seg_len = max(np.hypot(x2 - x1, y2 - y1), 1e-9)
        t = ((XX - x1) * (x2 - x1) + (YY - y1) * (y2 - y1)) / seg_len**2
        t = np.clip(t, 0.0, 1.0)
        dist = np.hypot(XX - (x1 + t * (x2 - x1)), YY - (y1 + t * (y2 - y1)))
        wall_mask |= dist <= res / 2.0

    # --- 出口开口 ---
    exit_cells: dict[str, list[tuple[int, int]]] = {}
    exit_segments: dict[str, tuple[float, float, float, float]] = {}
    for ex in scene["exits"]:
        seg = exit_segment(ex, geo["size"])
        exit_segments[ex["id"]] = seg
        x1, y1, x2, y2 = seg
        if ex["side"] in ("N", "S"):
            i0, i1 = int(np.ceil(x1 / res - 0.5)), int(np.floor(x2 / res - 0.5))
            j = Ny - 1 if ex["side"] == "N" else 0
            cells = [(i, j) for i in range(max(i0, 0), min(i1, Nx - 1) + 1)]
        else:
            j0, j1 = int(np.ceil(y1 / res - 0.5)), int(np.floor(y2 / res - 0.5))
            i = Nx - 1 if ex["side"] == "E" else 0
            cells = [(i, j) for j in range(max(j0, 0), min(j1, Ny - 1) + 1)]
        exit_cells[ex["id"]] = cells
        for (i, j) in cells:
            wall_mask[j, i] = False

    # Passage banks only add obstacles. They never erase fixed obstacles or walls.
    passage_mask = np.zeros((Ny, Nx), dtype=bool)
    for passage in _passages(scene):
        check_passage(passage)
        for bank in passage_banks(passage):
            passage_mask |= ((XX > bank["x"]) & (XX < bank["x"] + bank["w"])
                             & (YY > bank["y"]) & (YY < bank["y"] + bank["h"]))
    base_walkable = ~(fixed_mask | wall_mask | passage_mask)

    # --- 参数化设施（旋转矩形，格心判定） ---
    facility_mask = np.zeros((Ny, Nx), dtype=bool)
    facility_cells_by_id: dict[str, int] = {}
    for f in _facilities(scene):
        m = rotated_rect_mask(XX, YY, f["center"], f["length"], f["width"], f["rotation_deg"])
        facility_mask |= m
        facility_cells_by_id[f["id"]] = int(m.sum())

    obstacle_mask = fixed_mask | facility_mask | passage_mask
    walkable = ~(obstacle_mask | wall_mask)

    exit_blocked_by_facility = {
        ex_id: [(i, j) for (i, j) in cells if facility_mask[j, i]]
        for ex_id, cells in exit_cells.items()
    }

    # --- 人群分布 → 初始密度场 ---
    rho0 = np.zeros((Ny, Nx), dtype=np.float64)
    zone_id = np.full((Ny, Nx), -1, dtype=np.int32)
    crowd_mask = np.zeros((Ny, Nx), dtype=bool)
    zone_masks = []
    overlap = 0
    for zi, z in enumerate(scene["crowd"]["zones"]):
        raw = _poly_mask(XX, YY, z["polygon"])
        zone_masks.append(raw)
        crowd_mask |= raw
        m = raw & walkable                            # 被障碍/墙占住的地方不站人
        rho0[m] += z["count"] / (max(int(m.sum()), 1) * res * res)
        overlap += int(((zone_id >= 0) & m).sum())
        zone_id[m] = zi

    # 区域间有重叠时，整体缩放保证总人数正确
    target = float(sum(z["count"] for z in scene["crowd"]["zones"]))
    mass = float(rho0.sum()) * res * res
    if mass > 0 and abs(mass - target) / target > 1e-6:
        rho0 *= target / mass

    # --- 设施压到初始人群（以设施加入前的可行走范围为准） ---
    crowd_covered_by_zone = {}
    for zi, z in enumerate(scene["crowd"]["zones"]):
        occupiable = zone_masks[zi] & base_walkable
        covered = occupiable & facility_mask
        if covered.any():
            n = int(covered.sum())
            avail = int(occupiable.sum())
            crowd_covered_by_zone[z["id"]] = {
                "cells": n,
                "people": float(z["count"]) * n / max(avail, 1),
            }

    # --- 哈希：D 用来过滤重复布局，C 用来核对输入 ---
    grid = np.array([Nx, Ny, res], dtype=np.float64)
    exit_ids = sorted(exit_segments)
    exit_arr = np.array([exit_segments[k] for k in exit_ids], dtype=np.float64).reshape(-1, 4)
    mask_hash = _hash_parts([("obstacle_mask", obstacle_mask), ("grid", grid)])
    walkable_hash = _hash_parts([("walkable", walkable), ("grid", grid),
                                 ("exits", exit_arr)])
    input_hash = _hash_parts(
        [("walkable", walkable), ("rho0", rho0), ("zone_id", zone_id), ("grid", grid),
         ("exits", exit_arr)],
        meta={"params": scene["params"], "exit_ids": exit_ids, "res": res},
    )
    layout_value = facility_poses(scene)
    if _passages(scene):
        layout_value = {"facilities": layout_value, "passages": _passages(scene)}
    layout_hash = hashlib.sha256(_canonical_json(layout_value).encode("utf-8")).hexdigest()

    return {
        "Nx": Nx, "Ny": Ny, "res": res,
        "walkable": walkable, "base_walkable": base_walkable,
        "obstacle_mask": obstacle_mask, "fixed_obstacle_mask": fixed_mask,
        "facility_mask": facility_mask, "wall_mask": wall_mask, "passage_wall_mask": passage_mask,
        "rho0": rho0, "zone_id": zone_id, "crowd_mask": crowd_mask,
        "exit_cells": exit_cells, "exit_segments": exit_segments,
        "exit_blocked_by_facility": exit_blocked_by_facility,
        "facility_cells_by_id": facility_cells_by_id,
        "crowd_covered_by_zone": crowd_covered_by_zone,
        "overlap_cells": overlap,
        "mask_hash": mask_hash, "walkable_hash": walkable_hash,
        "input_hash": input_hash, "layout_hash": layout_hash,
    }


# ---------------------------------------------------------------- 连通性与完整校验

def _reachable(walkable: np.ndarray, seeds) -> np.ndarray:
    """从种子格 BFS（4 邻域），返回可达布尔图。"""
    Ny, Nx = walkable.shape
    seen = np.zeros((Ny, Nx), dtype=bool)
    q = deque()
    for (i, j) in seeds:
        if 0 <= i < Nx and 0 <= j < Ny and walkable[j, i] and not seen[j, i]:
            seen[j, i] = True
            q.append((i, j))
    while q:
        i, j = q.popleft()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if 0 <= ni < Nx and 0 <= nj < Ny and walkable[nj, ni] and not seen[nj, ni]:
                seen[nj, ni] = True
                q.append((ni, nj))
    return seen


def _clearance_field(walkable: np.ndarray, res: float) -> np.ndarray:
    """每格中心到最近不可走格中心的近似距离（米，8 邻域 Dijkstra）。

    返回 (Ny, Nx)；不可走格为 0。用于最小必要通路宽度检查。
    """
    Ny, Nx = walkable.shape
    dist = np.full((Ny, Nx), np.inf, dtype=np.float64)
    heap = []
    for j, i in zip(*np.where(~walkable)):
        dist[j, i] = 0.0
        heapq.heappush(heap, (0.0, int(i), int(j)))
    steps = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
             (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)),
             (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2)))
    while heap:
        d, i, j = heapq.heappop(heap)
        if d > dist[j, i]:
            continue
        for di, dj, w in steps:
            ni, nj = i + di, j + dj
            if 0 <= ni < Nx and 0 <= nj < Ny:
                nd = d + w
                if nd < dist[nj, ni]:
                    dist[nj, ni] = nd
                    heapq.heappush(heap, (nd, ni, nj))
    # 格心到障碍格边缘的近似净空：中心距 - 半格
    clearance = np.maximum((dist - 0.5) * res, 0.0)
    clearance[~walkable] = 0.0
    return clearance


def validate(scene: dict, R: dict | None = None) -> list[dict]:
    """完整几何校验。返回问题列表，error=必须修，warning=提示。空列表=通过。"""
    problems: list[dict] = []
    geo = scene["geometry"]
    L, H = geo["size"]
    res = geo["resolution"]
    policy = _layout_policy(scene)

    # 固定障碍在场地内 / 重叠
    for ob in geo.get("obstacles", []):
        if ob["x"] < 0 or ob["y"] < 0 or ob["x"] + ob["w"] > L + 1e-9 or ob["y"] + ob["h"] > H + 1e-9:
            problems.append(_err("error", f"障碍『{ob['name']}』超出场地边界"))
    obs = geo.get("obstacles", [])
    for a in range(len(obs)):
        for b in range(a + 1, len(obs)):
            A, B = obs[a], obs[b]
            if (A["x"] < B["x"] + B["w"] and B["x"] < A["x"] + A["w"] and
                    A["y"] < B["y"] + B["h"] and B["y"] < A["y"] + A["h"]):
                problems.append(_err("error", f"障碍『{A['name']}』与『{B['name']}』重叠"))

    # A passage owns its entire envelope. Existing structures are never carved away.
    envelopes = []
    banks = []
    for passage in _passages(scene):
        check_passage(passage)
        poly = passage_envelope(passage)
        label = passage["id"]
        if not polygon_inside_rect(poly, 0.0, 0.0, L, H):
            problems.append(_err("error", f"通道 {label} 的预留包络超出场地"))
        if passage["width"] < policy["min_passage_width"] - EPS:
            problems.append(_err("error", f"通道 {label} 宽度低于 min_passage_width"))
        for ob in obs:
            if convex_polygons_overlap(poly, rect_polygon(ob["x"], ob["y"], ob["w"], ob["h"])):
                problems.append(_err("error", f"通道 {label} 的预留包络与固定障碍冲突"))
        for a, other in envelopes:
            if convex_polygons_overlap(poly, other):
                problems.append(_err("error", f"通道 {label} 与 {a} 的预留包络重叠"))
        for x1, y1, x2, y2 in geo.get("walls", []):
            if passage_intersects_wall(passage, [x1, y1, x2, y2]):
                problems.append(_err("error", f"通道 {label} 的预留包络穿过固定隔墙"))
        envelopes.append((label, poly))
        for bank in passage_banks(passage):
            bank_poly = rect_polygon(bank["x"], bank["y"], bank["w"], bank["h"])
            for zone in scene["crowd"]["zones"]:
                if polygon_inside_polygon(bank_poly, zone["polygon"]) or any(
                        segment_intersects_polygon_interior(zone["polygon"][i - 1], vertex, bank_poly)
                        for i, vertex in enumerate(zone["polygon"])):
                    problems.append(_err("error", f"通道 {label} 隔挡覆盖初始人群区 {zone['id']}"))
            for ex in scene["exits"]:
                seg = exit_segment(ex, geo["size"])
                if segment_polygon_distance(seg[:2], seg[2:], bank_poly) < policy["exit_clearance"] - EPS:
                    problems.append(_err("error", f"通道 {label} 隔挡侵占出口净距"))
            banks.append(bank)
    # Facility collision checks also include passage banks.
    obs = list(obs) + banks

    # 出口贴墙角 / 互相重叠
    for ex in scene["exits"]:
        wall_len = L if ex["side"] in ("N", "S") else H
        if ex["offset"] < WALL_MARGIN - 1e-9 or ex["offset"] + ex["width"] > wall_len - WALL_MARGIN + 1e-9:
            problems.append(_err("error",
                f"出口 {ex['id']}『{ex['name']}』距墙角不足 {WALL_MARGIN} 米或超出墙面"))
    for a in range(len(scene["exits"])):
        for b in range(a + 1, len(scene["exits"])):
            ea, eb = scene["exits"][a], scene["exits"][b]
            if ea["side"] == eb["side"]:
                if ea["offset"] < eb["offset"] + eb["width"] and eb["offset"] < ea["offset"] + ea["width"]:
                    problems.append(_err("error",
                        f"出口 {ea['id']} 与 {eb['id']} 在{ea['side']}墙上的开口重叠"))

    for message in _variable_parameter_problems(scene):
        problems.append(_err("error", message))

    # 区域顶点在场地内
    for z in scene["crowd"]["zones"]:
        for (px, py) in z["polygon"]:
            if not (0 <= px <= L + 1e-9 and 0 <= py <= H + 1e-9):
                problems.append(_err("error", f"人群区 {z['id']}『{z['name']}』有顶点超出场地"))
                break

    # --- 参数化设施 ---
    facilities = _facilities(scene)
    ids = set()
    facility_polys = []
    for f in facilities:
        fid = f["id"]
        label = f"{fid}『{f['name']}』"
        if fid in ids:
            problems.append(_err("error", f"设施 id 重复：{fid}"))
        ids.add(fid)

        if not _finite(f["center"][0]) or not _finite(f["center"][1]) or \
                not _finite(f["rotation_deg"]) or f["length"] <= 0 or f["width"] <= 0:
            problems.append(_err("error", f"设施 {label} 的位姿/尺寸不是有限正数"))
            continue
        poly = facility_polygon(f["center"], f["length"], f["width"], f["rotation_deg"])
        facility_polys.append((f, poly))

        if not polygon_inside_rect(poly, 0.0, 0.0, L, H):
            problems.append(_err("error", f"设施 {label} 超出场地边界"))

        region = f.get("allowed_region")
        if region is not None:
            for (px, py) in region:
                if not (0 <= px <= L + 1e-9 and 0 <= py <= H + 1e-9):
                    problems.append(_err("error", f"设施 {label} 的 allowed_region 有顶点超出场地"))
                    break
            if not polygon_inside_polygon(poly, region):
                problems.append(_err("error", f"设施 {label} 超出允许移动区域"))

        if f["resizable"]:
            limits = f.get("size_limits") or {}
            for key in ("length", "width"):
                lim = limits.get(key)
                if lim and not (float(lim[0]) - 1e-9 <= float(f[key]) <= float(lim[1]) + 1e-9):
                    problems.append(_err("error",
                        f"设施 {label} 的 {key}={f[key]} 超出可改尺寸范围 {lim}"))

        for ob in obs:
            ob_poly = rect_polygon(ob["x"], ob["y"], ob["w"], ob["h"])
            if convex_polygons_overlap(poly, ob_poly):
                problems.append(_err("error", f"设施 {label} 与固定障碍『{ob['name']}』重叠"))

        for (x1, y1, x2, y2) in geo.get("walls", []):
            if segment_intersects_polygon_interior((x1, y1), (x2, y2), poly):
                problems.append(_err("error", f"设施 {label} 穿过内部隔墙"))

        for ex in scene["exits"]:
            seg = exit_segment(ex, geo["size"])
            d = segment_polygon_distance(seg[:2], seg[2:], poly)
            if d < policy["exit_clearance"] - 1e-9:
                problems.append(_err("error",
                    f"设施 {label} 距出口 {ex['id']}『{ex['name']}』仅 {d:.2f} 米，"
                    f"小于 {policy['exit_clearance']} 米"))

    for a in range(len(facility_polys)):
        fa, pa = facility_polys[a]
        for b in range(a + 1, len(facility_polys)):
            fb, pb = facility_polys[b]
            if convex_polygons_overlap(pa, pb):
                problems.append(_err("error",
                    f"设施 {fa['id']}『{fa['name']}』与 {fb['id']}『{fb['name']}』重叠"))

    # 栅格级校验
    R = R if R is not None else rasterize(scene)
    if R["overlap_cells"] > 0:
        problems.append(_err("warning",
            f"存在 {R['overlap_cells']} 个格子被多个区域覆盖，密度已按总人数归一化"))

    if (R["passage_wall_mask"] & R["crowd_mask"]).any():
        problems.append(_err("error", "通道隔挡覆盖初始人群，不允许重新分配人群掩盖布局冲突"))
    for ex_id, cells in R["exit_cells"].items():
        if any(R["passage_wall_mask"][j, i] for i, j in cells):
            problems.append(_err("error", f"通道隔挡覆盖出口 {ex_id}"))

    for f in facilities:
        if R["facility_cells_by_id"].get(f["id"], 0) == 0:
            problems.append(_err("warning",
                f"设施 {f['id']}『{f['name']}』在 {res} 米栅格上不可见（尺寸小于分辨率）"))

    for ex_id, cells in R["exit_blocked_by_facility"].items():
        if cells:
            problems.append(_err("error", f"设施覆盖了出口 {ex_id} 的开口，人员无法从该口离开"))

    for z_id, info in R["crowd_covered_by_zone"].items():
        problems.append(_err("error",
            f"设施压到初始人群区 {z_id}：{info['cells']} 个格子，约 {info['people']:.0f} 人；"
            f"第一版不允许用设施覆盖初始人群（请把该区域列为禁放区）"))

    seeds = [c for cells in R["exit_cells"].values() for c in cells]
    if not seeds:
        problems.append(_err("error", "没有任何出口成功开口，人员无法离开"))
    else:
        seen = _reachable(R["walkable"], seeds)
        jam = scene["params"].get("jam_density", DEFAULT_JAM)
        for zi, z in enumerate(scene["crowd"]["zones"]):
            m = (R["zone_id"] == zi)
            n_cells = int(m.sum())
            if n_cells == 0:
                problems.append(_err("error",
                    f"人群区 {z['id']}『{z['name']}』完全被障碍/墙覆盖，没有人能站进去"))
                continue
            if not bool(seen[m].any()):
                problems.append(_err("error",
                    f"人群区 {z['id']}『{z['name']}』的 {int(z['count'])} 人无法到达任何出口（被隔断）"))
            elif not bool(seen[m].all()):
                n_bad = int((m & ~seen).sum())
                problems.append(_err("warning",
                    f"人群区 {z['id']}『{z['name']}』有 {n_bad} 个格子不可达（局部死角）"))
            rho_max = float(R["rho0"][m].max()) if n_cells else 0.0
            if rho_max > jam:
                problems.append(_err("warning",
                    f"人群区 {z['id']}『{z['name']}』密度 {rho_max:.2f} 人/m² 超过拥堵阈值 {jam}，"
                    f"建议增大区域或减小人数"))

        # 最小必要通路宽度：出口本身允许较窄，但门内通道要够宽
        min_w = policy["min_passage_width"]
        if min_w > 0 and (facilities or _passages(scene)):
            clearance = _clearance_field(R["walkable"], res)
            wide = R["walkable"] & (clearance >= min_w / 2.0)
            exit_mask = np.zeros_like(R["walkable"])
            for cells in R["exit_cells"].values():
                for (i, j) in cells:
                    exit_mask[j, i] = True
            wide |= exit_mask & R["walkable"]
            seen_wide = _reachable(wide, seeds)
            for zi, z in enumerate(scene["crowd"]["zones"]):
                m = (R["zone_id"] == zi)
                if int(m.sum()) == 0 or not bool(seen[m].any()):
                    continue
                if not bool(seen_wide[m].any()):
                    problems.append(_err("error",
                        f"人群区 {z['id']}『{z['name']}』到出口的通道宽度不足 "
                        f"{min_w} 米（设施造成瓶颈）"))

    for ex_id, cells in R["exit_cells"].items():
        if not cells:
            problems.append(_err("error", f"出口 {ex_id} 宽度不足一个栅格，无法形成开口"))

    total_target = float(sum(z["count"] for z in scene["crowd"]["zones"]))
    total_mass = float(R["rho0"].sum()) * res * res
    if total_target > 0 and abs(total_mass - total_target) > max(1e-6 * total_target, 1e-9):
        problems.append(_err("warning",
            f"密度场总人数 {total_mass:.1f} 与配置人数 {total_target:.0f} 不一致"))

    return problems


# ---------------------------------------------------------------- 导出（交给 C）

def export_npz(scene: dict, out_path: str | Path) -> Path:
    """校验通过后导出 .npz：C 直接 np.load 使用。出错（error 级）时抛 RuntimeError。"""
    R = rasterize(scene)
    problems = validate(scene, R)
    errors = [p["msg"] for p in problems if p["level"] == "error"]
    if errors:
        raise RuntimeError("场景校验未通过，先修复以下问题：\n  - " + "\n  - ".join(errors))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    exit_ids = sorted(R["exit_segments"])
    exits_grid = np.array([R["exit_segments"][k] for k in exit_ids],
                          dtype=np.float32).reshape(-1, 4)
    exit_cells_json = json.dumps(
        {k: [list(c) for c in v] for k, v in R["exit_cells"].items()},
        ensure_ascii=False)
    np.savez_compressed(
        out_path,
        rho0=R["rho0"].astype(np.float32),
        walkable=R["walkable"],
        base_walkable=R["base_walkable"],
        obstacle_mask=R["obstacle_mask"],
        fixed_obstacle_mask=R["fixed_obstacle_mask"],
        facility_mask=R["facility_mask"],
        passage_wall_mask=R["passage_wall_mask"],
        zone_id=R["zone_id"],
        dx=np.float32(R["res"]),
        exits_grid=exits_grid,
        exit_ids=np.array(exit_ids),
        exit_cells_json=exit_cells_json,
        params=json.dumps(scene["params"], ensure_ascii=False),
        scene_json=json.dumps(scene, ensure_ascii=False),
        scene_version=str(scene.get("version", "")),
        scene_revision=np.int32(_scene_revision(scene)),
        layout_json=json.dumps(facility_poses(scene), ensure_ascii=False),
        passages_json=json.dumps(_passages(scene), ensure_ascii=False),
        variable_parameters_json=json.dumps(variable_parameters(scene) or [], ensure_ascii=False),
        layout_hash=R["layout_hash"],
        mask_hash=R["mask_hash"],
        walkable_hash=R["walkable_hash"],
        input_hash=R["input_hash"],
        crowd_covered_cells=np.int32(sum(v["cells"] for v in R["crowd_covered_by_zone"].values())),
        layout_policy_json=json.dumps(_layout_policy(scene), ensure_ascii=False),
    )
    return out_path


def total_people(rho0: np.ndarray, res: float) -> float:
    """密度场总质量 → 总人数（导出后核对用）。"""
    return float(rho0.sum()) * res * res


if __name__ == "__main__":  # 快速自检：python scene_io.py ../scenes/gym_example.json
    if len(sys.argv) != 2:
        print("用法: python scene_io.py <scene.json>")
        sys.exit(2)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sc = load_scene(sys.argv[1])
    probs = validate(sc)
    R = rasterize(sc)
    print(f"场景『{sc['meta']['name']}』(v{sc.get('version')}, rev {_scene_revision(sc)}): "
          f"网格 {R['Ny']}x{R['Nx']} @ {R['res']}m, "
          f"总人数 {total_people(R['rho0'], R['res']):.0f}, "
          f"设施 {len(_facilities(sc))} 个")
    print(f"  mask_hash={R['mask_hash'][:12]}  input_hash={R['input_hash'][:12]}")
    if probs:
        for p in probs:
            print(f"[{p['level'].upper()}] {p['msg']}")
    else:
        print("校验通过，无问题。")
