"""Run A and D in a child process and adapt their output to B's viewer."""

import json
import math
import subprocess
import sys
from pathlib import Path

from jsonschema import ValidationError

from .repository import load_json
from .trajectory_mock import SimulationInput, initial_agents, simulate_mock


def initial_scene(team_root: Path) -> dict:
    scene = load_json(team_root / "apps/web/scene-config/scenes/passage_optimization_example.json")
    scene["meta"]["name"] = "单层展厅"
    scene["meta"]["notes"] = "出口、通道净宽与展台编辑、栅格转换和单层人流评估。"
    scene["crowd"]["zones"][0]["count"] = 2
    return scene


def run_preview(team_root: Path, scene: dict, model_path: Path | None = None, parameter: dict | None = None) -> dict:
    command = [sys.executable, "-m", "apps.api.crowdturb.workbench", str(team_root.resolve())]
    if model_path:
        command.append(str(model_path.resolve()))
    request = scene if parameter is None else {"scene": scene, "parameter": parameter}
    try:
        result = subprocess.run(
            command,
            input=json.dumps(request, allow_nan=False),
            capture_output=True,
            encoding="utf-8",
            timeout=90 if model_path else 35,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("预演超时。") from error
    if result.returncode:
        raise ValueError(result.stdout.strip() or "A/D 模块执行失败，请检查依赖与模块版本。")
    return json.loads(result.stdout)


def build_preview(team_root: Path, scene: dict, simulate=simulate_mock, parameter=None) -> dict:
    import tempfile

    sys.path[:0] = [
        str(team_root / "apps/web/scene-config/python"),
        str(team_root / "packages/analysis"),
    ]
    from crowdturb_analysis.core import analyze
    from crowdturb_analysis.comparison import compare_candidates, modification_costs
    from crowdturb_analysis.sampling import auto_candidates
    from layout_eval import apply_layout
    from scene_io import load_scene, rasterize, validate, variable_parameters

    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "scene.json"
        source.write_text(json.dumps(scene, allow_nan=False), encoding="utf-8")
        scene = load_scene(source)

    for key in ("walk_speed", "T", "dt", "jam_density"):
        if type(scene["params"].get(key)) in (int, float):
            scene["params"][key] = float(scene["params"][key])

    length, height = scene["geometry"]["size"]
    resolution = scene["geometry"]["resolution"]
    if (length / resolution) * (height / resolution) > 12000:
        raise ValueError("预演最多支持 12000 个网格单元，请缩小场景或增大分辨率。")
    raster = rasterize(scene)
    problems = validate(scene, raster)
    errors = [problem["msg"] for problem in problems if problem["level"] == "error"]
    if errors:
        raise ValueError("场景校验失败：" + "；".join(errors))
    horizon = scene["params"]["T"]
    step = scene["params"].get("dt", 0.08)
    if not all(math.isfinite(value) and value > 0 for value in (horizon, step)):
        raise ValueError("评估时长和步长必须为正的有限数值。")
    agents = initial_agents(raster, sum(zone["count"] for zone in scene["crowd"]["zones"]))
    declared = variable_parameters(scene)
    if parameter is not None and not any(
        all(item[key] == value for key, value in parameter.items()) for item in declared
    ):
        raise ValueError("所选参数已不在当前场景的可调范围配置中。")
    reference_routes = {}

    def trajectory(layout, layout_raster, result_id, variant=0):
        resolution = float(layout_raster["res"])
        request = SimulationInput(
            result_id=result_id,
            scene_id="workbench-layout",
            revision=layout["meta"].get("revision", 1),
            input_hash=layout_raster["input_hash"],
            scene=layout,
            grid={"nx": layout_raster["Nx"], "ny": layout_raster["Ny"],
                  "dx": resolution, "dy": resolution, "origin": [0, 0]},
            walkable=layout_raster["walkable"].tolist(),
            exit_ids=tuple(sorted(layout_raster["exit_cells"])),
            exit_cells=layout_raster["exit_cells"],
            initial_agents=agents,
            seed=0,
            horizon_s=horizon,
            step_s=step,
            variant=variant,
        )
        if hasattr(simulate, "simulate_with_reference"):
            result, route = simulate.simulate_with_reference(request)
            if route:
                reference_routes[result_id] = route
            return result
        return simulate(request)

    policy = {
        "limits": {"exit_expansion_m": 1, "passage_added_area_m2": 8,
                   "facility_move_m": 14, "facility_rotation_deg": 0},
        "remaining_tolerance": 0,
        "congestion_tolerance": 1e-9,
    }
    baseline = trajectory(scene, raster, "baseline")
    real_run = baseline["source"] == "stddn"
    bindings = [parameter] if parameter is not None else None
    if bindings is None and real_run and declared:
        selected = next((row for row in declared if row["type"] == "exit_width"), declared[0])
        bindings = [{key: selected[key] for key in ("type", "target_id")}]
    report = auto_candidates(scene, bindings, points_per_parameter=3, max_candidates=27) if declared else {"candidates": []}
    runs = {}
    layouts = {}
    for index, candidate in enumerate(report["candidates"], start=1):
        if not candidate["solve_required"]:
            continue
        if real_run and any(cost > policy["limits"][key] + 1e-9
                            for key, cost in modification_costs(candidate).items()):
            continue
        candidate_id = candidate["candidate_id"]
        layout = apply_layout(scene, candidate["changes"])
        layouts[candidate_id] = layout
        try:
            runs[candidate_id] = trajectory(layout, rasterize(layout), candidate_id, index)
        except (ValueError, ValidationError) as error:
            runs[candidate_id] = {"status": "failed", "reason": str(error)}
        else:
            if real_run:
                break
    threshold = scene["params"].get("jam_density", 1.5)
    basis = "User scene parameter for integration; not a safety criterion"
    comparison = compare_candidates(scene, report, baseline, runs, policy, threshold, basis)
    recommendation = comparison["recommendation"]

    def artifacts(layout, trajectory):
        layout_raster = rasterize(layout)
        if layout_raster["input_hash"] != trajectory["input_hash"]:
            raise ValueError("A 栅格与 D 预演轨迹的输入哈希不一致。")
        widths = {exit_item["id"]: exit_item["width"] for exit_item in layout["exits"]}
        bundle = {
            "schema_version": "1.0.0",
            "scene_id": trajectory["scene_id"],
            "scene_revision": trajectory["scene_revision"],
            "grid": trajectory["grid"],
            "walkable": layout_raster["walkable"].tolist(),
            "obstacle_mask": layout_raster["obstacle_mask"].tolist(),
            "initial_density": layout_raster["rho0"].tolist(),
            "exits": [
                {"exit_id": exit_id, "cells": cells, "width_m": widths[exit_id]}
                for exit_id, cells in sorted(layout_raster["exit_cells"].items())
            ],
            "provenance": {
                "input_hash": layout_raster["input_hash"],
                "mask_hash": layout_raster["mask_hash"],
            },
        }
        threshold = comparison["threshold"]
        field, analysis = analyze(trajectory, threshold["value"], threshold["basis"])
        view = {"scene": bundle, "trajectory": trajectory, "field": field, "analysis": analysis}
        route = reference_routes.get(trajectory["result_id"])
        if route:
            view["route"] = route
        return view

    views = {"baseline": artifacts(scene, baseline)}
    if real_run or parameter is not None:
        for row in comparison["candidates"]:
            candidate_id = row["candidate_id"]
            if row.get("result_id"):
                views[candidate_id] = artifacts(layouts[candidate_id], runs[candidate_id])
    elif recommendation in runs:
        views[recommendation] = artifacts(layouts[recommendation], runs[recommendation])
    return {"scene": scene, "views": views, "comparison": comparison, "problems": problems}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        # A running workbench from before parameter selection sends the scene directly.
        scene = request.get("scene", request)
        simulation = simulate_mock
        if len(sys.argv) > 2:
            from .trajectory_onnx import STDDN

            simulation = STDDN(Path(sys.argv[2]))
        preview = build_preview(Path(sys.argv[1]), scene, simulation, request.get("parameter"))
        print(json.dumps(preview, ensure_ascii=False, allow_nan=False))
    except (ImportError, OSError, ValueError, ValidationError, KeyError, TypeError) as error:
        print(str(error))
        raise SystemExit(1)
