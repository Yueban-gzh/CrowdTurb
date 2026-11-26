"""A reproducible software demonstration with hand-authored outcomes, never a simulator."""

import copy
import hashlib

from .core import initial_state_hash
from .sampling import auto_candidates
from .comparison import compare_candidates
from scene_io import rasterize
from layout_eval import apply_layout


def mock_trajectory(scene, result_id, outcome="baseline"):
    """Synthetic snapshots/events chosen to exercise decisions; no motion integration."""
    r = rasterize(scene)
    agents = [{"agent_id": "a", "position_m": [10.1, 15.1], "velocity_mps": [0.0, 0.0]},
              {"agent_id": "b", "position_m": [10.2, 15.1], "velocity_mps": [0.0, 0.0]}]
    frames = [{"time_s": 0, "agents": copy.deepcopy(agents)}]
    if outcome == "baseline":
        frames.extend([{"time_s": t, "agents": [copy.deepcopy(agents[1])]} for t in [1, 2]])
        events = [{"agent_id": "a", "exit_id": "E1", "time_s": 0.5}]
    elif outcome == "improved":
        frames.extend([{"time_s": t, "agents": []} for t in [1, 2]])
        events = [{"agent_id": a["agent_id"], "exit_id": "E1", "time_s": 0.5} for a in agents]
    elif outcome in ("tradeoff", "worsened"):
        later = copy.deepcopy(agents)
        later[1]["position_m"] = [11.2, 15.1]
        if outcome == "tradeoff":
            frames.extend([{"time_s": 1, "agents": later}, {"time_s": 2, "agents": []}])
            events = [{"agent_id": a["agent_id"], "exit_id": "E1", "time_s": 1.5} for a in agents]
        else:
            frames.extend([{"time_s": t, "agents": copy.deepcopy(later)} for t in [1, 2]])
            events = []
    else:
        raise ValueError(outcome)
    return {"schema_version": "0.2.0", "result_id": result_id, "scene_id": "mock-workflow-hall",
            "scene_revision": scene["meta"]["revision"], "input_hash": r["input_hash"],
            "initial_state_hash": initial_state_hash(agents),
            "behavior_hash": hashlib.sha256(b"same synthetic fixed behavior attributes").hexdigest(),
            "source": "mock", "population_mode": "fixed_closed", "model_sha256": None, "reproducibility_record": None,
            "simulation_settings": {"dt_s": 0.08, "seed": 0, "integrator": "mock-not-integrated",
                                    "planner_version": "none-mock", "collision_version": "none-mock"},
            "grid": {"nx": r["Nx"], "ny": r["Ny"], "dx": r["res"], "dy": r["res"], "origin": [0, 0]},
            "walkable": r["walkable"].tolist(), "exit_ids": sorted(r["exit_cells"]), "frames": frames, "exit_events": events}


def build_demo(scene):
    scene = copy.deepcopy(scene)
    scene["meta"]["notes"] = "SYNTHETIC WORKFLOW ONLY: 2 people and hand-authored outcomes, not a physical simulation"
    scene["crowd"]["zones"][0]["count"] = 2
    report = auto_candidates(scene, points_per_parameter=3, max_candidates=27)
    baseline = mock_trajectory(scene, "mock-demo-baseline")
    policy = {"limits": {"exit_expansion_m": 1, "passage_added_area_m2": 8, "facility_move_m": 14,
                         "facility_rotation_deg": 0}, "remaining_tolerance": 0, "congestion_tolerance": 1e-9}
    runs = {}
    for index, row in enumerate(report["candidates"]):
        if not row["solve_required"]:
            continue
        mode = index % 7
        if mode == 5:
            continue
        if mode == 4:
            runs[row["candidate_id"]] = {"status": "failed", "reason": "Synthetic failure, no simulator called"}
            continue
        outcome = ["improved", "baseline", "worsened", "tradeoff"][mode] if mode < 4 else "improved"
        trace = mock_trajectory(apply_layout(scene, row["changes"]), "mock-demo-" + row["candidate_id"], outcome)
        if mode == 6:
            trace["simulation_settings"]["seed"] = 999
        runs[row["candidate_id"]] = trace
    comparison = compare_candidates(scene, report, baseline, runs, policy, 1.5,
                                    "Synthetic demonstration threshold; not a safety criterion")
    return {"scene": scene, "candidates": report, "baseline": baseline, "runs": runs,
            "policy": policy, "comparison": comparison}
