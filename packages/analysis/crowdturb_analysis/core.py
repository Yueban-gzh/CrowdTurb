"""Analyze complete, fixed-population trajectories; never infer evacuation from absence.

Sampling convention: frames describe active people after exits at the same timestamp.
Each frame is held on [time_i, time_(i+1)); the last frame has no assumed duration.
"""

from __future__ import annotations

import math
import hashlib
import json
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[3]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_finite(value):
    if isinstance(value, float):
        require(math.isfinite(value), "Non-finite numeric value")
    elif isinstance(value, dict):
        for item in value.values():
            check_finite(item)
    elif isinstance(value, list):
        for item in value:
            check_finite(item)


def check_schema(value, name):
    check_finite(value)
    schema = json.loads((REPO / "contracts" / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)


def initial_state_hash(agents):
    """Canonical sorted initial IDs, positions and velocities; excludes layout and results."""
    rows = [{"agent_id": a["agent_id"],
             "position_m": [float(v) if v else 0.0 for v in a["position_m"]],
             "velocity_mps": [float(v) if v else 0.0 for v in a["velocity_mps"]]}
            for a in sorted(agents, key=lambda a: a["agent_id"])]
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_trajectory(data):
    check_schema(data, "trajectory")
    grid = data["grid"]
    nx, ny = grid["nx"], grid["ny"]
    walkable = data["walkable"]
    require(len(walkable) == ny and all(len(row) == nx for row in walkable), "Grid/mask shape mismatch")
    require(any(any(row) for row in walkable), "No walkable cells")
    frames = data["frames"]
    require(frames[0]["time_s"] == 0, "First trajectory frame must be at t=0")
    initial_ids = [a["agent_id"] for a in frames[0]["agents"]]
    require(len(set(initial_ids)) == len(initial_ids), "Duplicate initial agent ID")
    require(data["initial_state_hash"] == initial_state_hash(frames[0]["agents"]), "Initial state hash mismatch")
    initial_ids = set(initial_ids)
    end = frames[-1]["time_s"]
    events = {}
    for event in data["exit_events"]:
        aid = event["agent_id"]
        require(aid in initial_ids, "Exit event for unknown person")
        require(aid not in events, "Duplicate exit event")
        require(event["exit_id"] in data["exit_ids"], "Exit event for unknown exit")
        require(0 < event["time_s"] <= end, "Exit event outside recorded interval")
        events[aid] = event
    previous = -1.0
    for frame in frames:
        time = frame["time_s"]
        require(time > previous, "Frame times must strictly increase")
        previous = time
        ids = [a["agent_id"] for a in frame["agents"]]
        require(len(ids) == len(set(ids)), "Duplicate person within frame")
        expected = {aid for aid in initial_ids if aid not in events or events[aid]["time_s"] > time}
        require(set(ids) == expected, "Missing/new person without a matching exit event; incomplete trace")
        for agent in frame["agents"]:
            x, y = agent["position_m"]
            ox, oy = grid["origin"]
            ix = math.floor((x - ox) / grid["dx"])
            iy = math.floor((y - oy) / grid["dy"])
            require(0 <= ix < nx and 0 <= iy < ny, "Active person outside grid; not an exit event")
            require(walkable[iy][ix], "Active person inside an obstacle")
    return events


def analyze(data, threshold, threshold_basis):
    """Return (derived density/velocity field, independent analysis result)."""
    require(isinstance(threshold, (int, float)) and not isinstance(threshold, bool)
            and math.isfinite(threshold) and threshold > 0, "Threshold must be a finite positive density")
    require(isinstance(threshold_basis, str) and bool(threshold_basis.strip()), "State the threshold source or assumption")
    events = validate_trajectory(data)
    grid = data["grid"]
    nx, ny = grid["nx"], grid["ny"]
    area = grid["dx"] * grid["dy"]
    require(math.isfinite(area) and area > 0, "Invalid cell area")
    frames, times, remaining, congested = [], [], [], []
    densities = []
    for frame in data["frames"]:
        count = np.zeros((ny, nx), dtype=np.int64)
        velocity = np.zeros((ny, nx, 2), dtype=float)
        for agent in frame["agents"]:
            x, y = agent["position_m"]
            ix = math.floor((x - grid["origin"][0]) / grid["dx"])
            iy = math.floor((y - grid["origin"][1]) / grid["dy"])
            count[iy, ix] += 1
            velocity[iy, ix] += agent["velocity_mps"]
        np.divide(velocity, count[:, :, None], out=velocity, where=count[:, :, None] > 0)
        density = count / area
        require(np.isfinite(density).all() and np.isfinite(velocity).all(), "Aggregation overflow")
        frames.append({"time_s": frame["time_s"], "density": density.tolist(), "velocity": velocity.tolist()})
        times.append(frame["time_s"])
        remaining.append(len(frame["agents"]))
        congested.append(float(np.count_nonzero(density > threshold) * area))
        densities.append(density)
    dt = np.diff(times)
    congestion_integral = float(np.dot(np.array(congested[:-1]), dt))
    require(math.isfinite(congestion_integral), "Congestion integral overflow")
    source = data["source"]
    common = {"schema_version": "0.2.0", "scene_id": data["scene_id"],
              "scene_revision": data["scene_revision"], "input_hash": data["input_hash"],
              "source": source, "derived_from": data["result_id"],
              "initial_state_hash": data["initial_state_hash"],
              "model_sha256": data["model_sha256"], "simulation_settings": data["simulation_settings"],
              "reproducibility_record": data["reproducibility_record"]}
    field = {**common, "result_id": data["result_id"] + "-field", "grid": grid,
             "walkable": data["walkable"], "frames": frames,
             "method": "cell count / full walkable cell area; per-cell arithmetic mean velocity; empty=zero"}

    def metric(value, unit, definition):
        return {"value": value, "unit": unit, "definition": definition,
                "source": source, "status": "normal"}

    def series(key, values, unit):
        return {"metric_id": key, "time_s": times, "values": values, "unit": unit,
                "source": source, "status": "normal", "interpolation": "previous_sample"}

    all_events = list(events.values())
    series_items = [series("remaining_people", remaining, "person"),
                    series("congested_area", congested, "m2")]
    for exit_id in data["exit_ids"]:
        cumulative = [sum(e["exit_id"] == exit_id and e["time_s"] <= t for e in all_events) for t in times]
        series_items.append(series("exit_cumulative:" + exit_id, cumulative, "person"))
    hotspots = []
    for iy in range(ny):
        for ix in range(nx):
            start, duration, peak = None, 0.0, 0.0
            for i, density in enumerate(densities):
                value = float(density[iy, ix])
                if value > threshold:
                    if start is None:
                        start = times[i]
                    peak = max(peak, value)
                    duration += float(dt[i]) if i < len(dt) else 0.0
                if start is not None and (value <= threshold or i == len(densities) - 1):
                    hotspots.append({"ix": ix, "iy": iy, "start_time_s": start, "end_time_s": times[i],
                                     "observed_duration_s": duration, "peak_density": peak, "unit": "person/m2",
                                     "source": source, "status": "normal"})
                    start, duration, peak = None, 0.0, 0.0
    output = {**common, "analysis_id": data["result_id"] + "-analysis", "status": "normal",
              "method_version": "trajectory-grid-0.1.0", "time_range_s": [times[0], times[-1]],
              "grid": grid,
              "threshold": {"value": threshold, "unit": "person/m2", "comparison": ">", "basis": threshold_basis},
              "metrics": {
                  "remaining_at_end": metric(remaining[-1], "person", "Active agents at the final recorded time; horizon is explicit"),
                  "congestion_area_time": metric(congestion_integral, "m2*s", "Area above threshold integrated with previous-sample hold"),
                  "exited_people": metric(len(events), "person", "Unique explicit exit events in the recorded interval"),
              }, "series": series_items, "hotspots": hotspots}
    check_schema(output, "analysis")
    return field, output
