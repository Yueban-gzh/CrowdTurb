"""Deterministic synthetic trajectories for testing the integration contract."""

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class SimulationInput:
    result_id: str
    scene_id: str
    revision: int
    input_hash: str
    scene: dict
    grid: dict
    walkable: list
    exit_ids: tuple
    exit_cells: dict
    initial_agents: list
    seed: int
    horizon_s: float
    step_s: float
    variant: int = 0


def initial_agents(raster, people):
    if not 0 <= people <= 500:
        raise ValueError("Mock 评估支持 0 至 500 人。")
    if not people:
        return []
    density = raster["rho0"]
    total = float(density.sum())
    if total <= 0:
        raise ValueError("初始人群没有可用的栅格单元。")
    agents = []
    cumulative = 0.0
    resolution = float(raster["res"])
    for iy, row in enumerate(density):
        for ix, value in enumerate(row):
            cumulative += float(value)
            while len(agents) < people and (len(agents) + 0.5) * total / people <= cumulative:
                agents.append({
                    "agent_id": f"mock-{len(agents):04d}",
                    "position_m": [(ix + 0.5) * resolution, (iy + 0.5) * resolution],
                    "velocity_mps": [0.0, 0.0],
                })
    if len(agents) != people:
        raise ValueError("Mock 无法从初始密度生成指定人数。")
    return agents


def simulate_mock(request: SimulationInput) -> dict:
    agents = request.initial_agents
    exits = request.exit_ids
    leaving = (len(agents) + 1) // 2
    if request.variant % 3 == 1:
        leaving = len(agents)
    elif request.variant % 3 == 2:
        leaving = 0
    if not exits:
        leaving = 0
    cutoff = request.horizon_s / 4
    canonical = json.dumps(agents, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": "0.2.0",
        "result_id": request.result_id,
        "scene_id": request.scene_id,
        "scene_revision": request.revision,
        "input_hash": request.input_hash,
        "initial_state_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "behavior_hash": hashlib.sha256(b"crowdturb-mock-stationary-v1").hexdigest(),
        "source": "mock",
        "population_mode": "fixed_closed",
        "model_sha256": None,
        "reproducibility_record": None,
        "simulation_settings": {
            "dt_s": request.step_s,
            "seed": request.seed,
            "integrator": "mock-scheduled-exits-v1",
            "planner_version": "none-mock",
            "collision_version": "none-mock",
        },
        "grid": request.grid,
        "walkable": request.walkable,
        "exit_ids": list(exits),
        "frames": [
            {"time_s": 0, "agents": agents},
            {"time_s": request.horizon_s / 2, "agents": agents[leaving:]},
            {"time_s": request.horizon_s, "agents": agents[leaving:]},
        ],
        "exit_events": [
            {"agent_id": agent["agent_id"], "exit_id": exits[0], "time_s": cutoff}
            for agent in agents[:leaving]
        ],
    }
