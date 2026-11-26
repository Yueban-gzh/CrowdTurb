"""Evaluate recorded candidate trajectories against reconstructed expected scenes.

No simulator is called. Missing, failed, incompatible and duplicate inputs remain
explicit. Recommendations concern only supplied comparable runs under the policy.
"""

import math
import numpy as np
from jsonschema import ValidationError

from .core import analyze, check_schema, validate_trajectory
from .density import estimator_config
from . import candidates as _candidate_adapter  # Makes A's module path available.
from scene_io import rasterize, validate
from layout_eval import apply_layout, evaluate_candidates

COST_KEYS = ("exit_expansion_m", "passage_added_area_m2", "facility_move_m", "facility_rotation_deg")


def expected_input_errors(scene, trajectory, scene_id):
    """Validate actual mask/grid/exit IDs against A, not only C's self-reported hash."""
    validate_trajectory(trajectory)
    r = rasterize(scene)
    expected_grid = {"nx": r["Nx"], "ny": r["Ny"], "dx": r["res"], "dy": r["res"], "origin": [0, 0]}
    errors = []
    for field, expected in (("scene_id", scene_id), ("scene_revision", scene.get("meta", {}).get("revision", 1)),
                            ("input_hash", r["input_hash"]), ("grid", expected_grid),
                            ("exit_ids", sorted(r["exit_cells"]))):
        actual = sorted(trajectory[field]) if field == "exit_ids" else trajectory[field]
        if actual != expected:
            errors.append(f"Unexpected {field} for submitted scene")
    if not np.array_equal(np.asarray(trajectory["walkable"]), r["walkable"]):
        errors.append("Trajectory walkable mask differs from submitted scene")
    expected_people = sum(z["count"] for z in scene["crowd"]["zones"])
    if abs(expected_people - round(expected_people)) > 1e-9 or len(trajectory["frames"][0]["agents"]) != round(expected_people):
        errors.append("Initial person count differs from submitted scene")
    if trajectory["grid"] == expected_grid:
        for agent in trajectory["frames"][0]["agents"]:
            ix = math.floor(agent["position_m"][0] / r["res"])
            iy = math.floor(agent["position_m"][1] / r["res"])
            if r["rho0"][iy, ix] <= 0:
                errors.append("Initial person outside configured crowd support")
                break
    return errors


def comparability_errors(baseline, candidate):
    errors = []
    for key in ("scene_id", "source", "initial_state_hash", "model_sha256", "simulation_settings", "grid", "behavior_hash"):
        if key == "behavior_hash" and (not baseline.get(key) or not candidate.get(key)):
            errors.append("Missing behavior_hash for initial targets/desired speeds/history and other behavior attributes")
        elif baseline.get(key) != candidate.get(key):
            errors.append(f"Different {key}")
    if [f["time_s"] for f in baseline["frames"]] != [f["time_s"] for f in candidate["frames"]]:
        errors.append("Different observation times/horizon")
    if baseline["simulation_settings"]["seed"] is None or candidate["simulation_settings"]["seed"] is None:
        errors.append("Explicit common seed required for this single-pair comparison")
    return errors


def modification_costs(report):
    return {
        "exit_expansion_m": sum(max(0.0, d["width_delta_m"]) for d in report["exit_width_deltas"]),
        "passage_added_area_m2": sum(max(0.0, d["clear_area_delta_m2"]) for d in report["passage_width_deltas"]),
        "facility_move_m": report["move_distance_m"],
        "facility_rotation_deg": report["rotation_delta_deg"],
    }


def compare_candidates(scene, candidate_report, baseline, runs, policy, threshold, threshold_basis,
                       *, density_method="cell", bandwidth_m=None):
    if any(p["level"] == "error" for p in validate(scene)):
        raise ValueError("Infeasible baseline scene")
    if not isinstance(runs, dict):
        raise ValueError("runs must map candidate IDs to trajectory or explicit failure")
    if not isinstance(policy, dict) or set(policy) != {"limits", "remaining_tolerance", "congestion_tolerance"}:
        raise ValueError("Policy requires explicit limits and remaining/congestion absolute tolerances")
    limits = policy["limits"]
    if not isinstance(limits, dict) or set(limits) != set(COST_KEYS):
        raise ValueError("Declare all four modification limits; null explicitly means unconstrained")
    for value in list(limits.values()) + [policy["remaining_tolerance"], policy["congestion_tolerance"]]:
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError("Limits/tolerances must be finite nonnegative numbers or null limits")
    if policy["remaining_tolerance"] is None or policy["congestion_tolerance"] is None:
        raise ValueError("Tolerances cannot be null")
    estimator_config(density_method, bandwidth_m)
    # Recompute feasibility, hashes and deltas from actual changes rather than trust a report.
    source_candidates = candidate_report.get("candidates")
    if not isinstance(source_candidates, list):
        raise ValueError("Candidate report needs candidates")
    ids = [r.get("candidate_id") for r in source_candidates]
    if any(not isinstance(cid, str) or not cid or cid == "baseline" for cid in ids) or len(ids) != len(set(ids)):
        raise ValueError("Candidate IDs must be unique nonempty strings; baseline is reserved")
    if set(runs) - set(ids):
        raise ValueError("Run supplied for unknown candidate")
    report = evaluate_candidates(scene, [{"candidate_id": r["candidate_id"], "changes": r["changes"]}
                                         for r in source_candidates])
    baseline_errors = expected_input_errors(scene, baseline, baseline["scene_id"])
    if baseline_errors:
        raise ValueError("Baseline input mismatch: " + "; ".join(baseline_errors))
    _, base_analysis = analyze(baseline, threshold, threshold_basis, density_method=density_method, bandwidth_m=bandwidth_m)
    bm = base_analysis["metrics"]
    rows, eligible = [], []
    seen_result_ids = {baseline["result_id"]}
    for item in report["candidates"]:
        cid = item["candidate_id"]
        row = {"candidate_id": cid, "changes": item["changes"], "input_hash": item["input_hash"],
               "source": baseline["source"], "status": "pending", "reasons": [], "costs": modification_costs(item),
               "duplicate_of": item["duplicate_of"], "metrics": None, "deltas": None,
               "improvement_percent": None, "tradeoff": False, "rank": None}
        rows.append(row)
        if not item["feasible"]:
            row.update(status="invalid", reasons=[p["msg"] for p in item["problems"] if p["level"] == "error"])
            continue
        if any(any(abs(value) > 1e-9 for value in pose["size_delta"]) for pose in item["pose_delta"]):
            row.update(status="incomparable", reasons=["Facility resizing has no agreed modification-cost policy; compare fixed-size facilities only"])
            continue
        exceeded = [key for key, limit in limits.items() if limit is not None and row["costs"][key] > limit + 1e-9]
        if exceeded:
            row.update(status="over_budget", reasons=["Modification budget exceeded: " + key for key in exceeded])
            continue
        if item["duplicate_of"]:
            row.update(status="duplicate", reasons=["Equivalent solver input; no independent improvement claim"])
            continue
        if cid not in runs:
            row["reasons"] = ["No complete trajectory supplied"]
            continue
        trajectory = runs[cid]
        if not isinstance(trajectory, dict):
            row.update(status="failed", reasons=["Run must be a trajectory object or explicit failure"])
            continue
        if trajectory.get("status") == "failed":
            reason = trajectory.get("reason")
            row.update(status="failed", reasons=[reason if isinstance(reason, str) and reason.strip() else "Unspecified simulation failure"])
            continue
        if trajectory.get("source") in ("mock", "stddn"):
            row["source"] = trajectory["source"]
        try:
            candidate_scene = apply_layout(scene, item["changes"])
            errors = expected_input_errors(candidate_scene, trajectory, baseline["scene_id"])
            errors.extend(comparability_errors(baseline, trajectory))
            if trajectory["result_id"] in seen_result_ids:
                errors.append("Result ID already belongs to baseline or another candidate")
            seen_result_ids.add(trajectory["result_id"])
            if errors:
                row.update(status="incomparable", reasons=errors)
                continue
            _, analysis = analyze(trajectory, threshold, threshold_basis, density_method=density_method, bandwidth_m=bandwidth_m)
        except (ValueError, ValidationError, KeyError, TypeError) as exc:
            row.update(status="failed", reasons=[str(exc)])
            continue
        row["metrics"] = analysis["metrics"]
        row["result_id"] = trajectory["result_id"]
        keys = ("remaining_at_end", "congestion_area_time")
        delta = {key: analysis["metrics"][key]["value"] - bm[key]["value"] for key in keys}
        percent = {key: (-100 * delta[key] / bm[key]["value"] if bm[key]["value"] else None) for key in keys}
        row.update(deltas=delta, improvement_percent=percent)
        tolerances = (policy["remaining_tolerance"], policy["congestion_tolerance"])
        better = any(delta[k] < -tol for k, tol in zip(keys, tolerances))
        worse = any(delta[k] > tol for k, tol in zip(keys, tolerances))
        if better and worse:
            row.update(status="tradeoff", tradeoff=True, reasons=["Some effectiveness metrics improve while others worsen; human choice required"])
        elif worse:
            row["status"] = "worsened"
        elif better:
            row["status"] = "improved"
            eligible.append(row)
        else:
            row["status"] = "unchanged"
    eligible.sort(key=lambda r: (r["metrics"]["remaining_at_end"]["value"],
                                 r["metrics"]["congestion_area_time"]["value"], r["candidate_id"]))
    for rank, row in enumerate(eligible, 1):
        row["rank"] = rank
    output = {"schema_version": "0.1.0", "source": baseline["source"], "scene_id": baseline["scene_id"],
              "baseline_result_id": baseline["result_id"], "baseline_input_hash": baseline["input_hash"],
              "baseline_metrics": bm, "time_range_s": base_analysis["time_range_s"], "threshold": base_analysis["threshold"],
              "density_estimator": base_analysis["density_estimator"], "policy": policy,
              "ranking_rule": "Only comparable, within-budget candidates that improve at least one effectiveness metric without worsening the other; remaining people then congestion; ID breaks exact ties",
              "recommendation": eligible[0]["candidate_id"] if eligible else "baseline",
              "recommendation_scope": "Supplied recorded runs only; baseline retained when no eligible improvement. No global optimality or real-world safety claim.",
              "candidates": rows}
    check_schema(output, "comparison")
    return output
