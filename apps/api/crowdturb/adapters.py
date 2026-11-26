"""Replaceable solver adapters for the system task runner."""

from __future__ import annotations

import copy
from pathlib import Path

from .repository import load_json, validate_result


class SolverNotConfigured(RuntimeError):
    """Raised when a module has not supplied a runnable solver adapter yet."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(message)
        self.code = "solver_not_configured"
        self.source = source


class MockAdapter:
    """Return the hand-authored fixture while preserving its Mock identity."""

    def __init__(self, fixtures: Path) -> None:
        self.fixture_path = fixtures / "field.mock.json"

    def solve(self, scene: dict, task_id: str) -> dict:
        result = copy.deepcopy(load_json(self.fixture_path))
        result["result_id"] = f"mock-{task_id}"
        result["scene_id"] = scene["scene_id"]
        result["scene_revision"] = scene["revision"]
        result["source"] = "mock"
        result["description"] = (
            "系统底座 Mock 联调结果；由手工样例生成，不代表 PDE、FNO 或真实人数。"
        )
        result["provenance"] = {
            "generator": "crowdturb-system-mock-adapter-v1",
            "reproducibility_record": None,
        }
        validate_result(result, scene)
        return result


class UnconfiguredAdapter:
    def __init__(self, source: str, owner: str) -> None:
        self.source = source
        self.owner = owner

    def solve(self, scene: dict, task_id: str) -> dict:
        raise SolverNotConfigured(
            self.source,
            f"{self.source.upper()} 求解适配器尚未接入。请由 {self.owner} 提供独立运行入口后，"
            "在 apps/api/crowdturb/adapters.py 中替换此占位实现。",
        )


def build_adapters(fixtures: Path) -> dict[str, object]:
    return {
        "mock": MockAdapter(fixtures),
        "pde": UnconfiguredAdapter("pde", "C"),
        "fno": UnconfiguredAdapter("fno", "C"),
    }
