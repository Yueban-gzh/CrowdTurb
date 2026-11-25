"""Validate the repository's draft contracts and supporting engineering records."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> dict:
    def reject_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON number: {value}")

    def unique_keys(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=unique_keys,
    )


def finite_numbers(value: object) -> None:
    if isinstance(value, float):
        require(math.isfinite(value), "Numeric values must be finite")
    elif isinstance(value, dict):
        for item in value.values():
            finite_numbers(item)
    elif isinstance(value, list):
        for item in value:
            finite_numbers(item)


def validate_schema(data: dict, name: str) -> None:
    finite_numbers(data)
    schema = load_json(ROOT / "contracts" / f"{name}.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(data)


def check_shape(matrix: list, nx: int, ny: int, label: str) -> None:
    require(len(matrix) == ny, f"{label}: expected {ny} rows")
    require(all(len(row) == nx for row in matrix), f"{label}: expected {nx} columns")


def validate_scene(scene: dict) -> None:
    validate_schema(scene, "scene")
    grid = scene["grid"]
    nx, ny = grid["nx"], grid["ny"]
    check_shape(scene["walkable"], nx, ny, "walkable")
    check_shape(scene["initial_density"], nx, ny, "initial_density")
    for y, row in enumerate(scene["walkable"]):
        for x, walkable in enumerate(row):
            require(walkable or scene["initial_density"][y][x] == 0, "Density in obstacle")

    exits = {}
    occupied = set()
    capacity_by_exit = {}
    for exit_ in scene["exits"]:
        key = exit_["exit_id"]
        require(key not in exits, f"Duplicate exit ID: {key}")
        exits[key] = exit_
        for x, y in exit_["cells"]:
            require(x < nx and y < ny, "Exit cell outside grid")
            require(scene["walkable"][y][x], "Exit cell is blocked")
            require((x, y) not in occupied, "Overlapping exit cells")
            occupied.add((x, y))
        cells = exit_["cells"]
        capacities = []
        if all(x == 0 for x, _ in cells) or all(x == nx - 1 for x, _ in cells):
            capacities.append(len(cells) * grid["dy"])
        if all(y == 0 for _, y in cells) or all(y == ny - 1 for _, y in cells):
            capacities.append(len(cells) * grid["dx"])
        require(bool(capacities), "Exit cells must share one outer boundary")
        capacity_by_exit[key] = max(capacities)
        require(exit_["width_m"] <= capacity_by_exit[key], "Exit width exceeds boundary span")

    parameters = set()
    for parameter in scene["adjustable_parameters"]:
        key = parameter["parameter_id"]
        require(key not in parameters, "Duplicate parameter ID")
        parameters.add(key)
        target = parameter["target_id"]
        require(target in exits, "Unknown parameter target")
        require(parameter["min_m"] <= exits[target]["width_m"] <= parameter["max_m"], "Current width outside adjustable range")
        require(parameter["max_m"] <= capacity_by_exit[target], "Adjustable width exceeds boundary span")


def validate_result(result: dict, scene: dict) -> None:
    validate_schema(result, "field-result")
    require(result["scene_id"] == scene["scene_id"], "Result refers to another scene")
    require(result["scene_revision"] == scene["revision"], "Result refers to another scene revision")
    nx, ny = scene["grid"]["nx"], scene["grid"]["ny"]
    previous = -1.0
    for frame in result["frames"]:
        require(frame["time_s"] > previous, "Frame times must strictly increase")
        previous = frame["time_s"]
        for field in ("density", "velocity"):
            check_shape(frame[field], nx, ny, field)
        for y, row in enumerate(scene["walkable"]):
            for x, walkable in enumerate(row):
                if not walkable:
                    require(frame["density"][y][x] == 0, "Frame density in obstacle")
                    require(frame["velocity"][y][x] == [0, 0], "Frame velocity in obstacle")


def validate_task(task: dict, scene: dict, result: dict | None = None) -> None:
    validate_schema(task, "task")
    require(task["scene_id"] == scene["scene_id"], "Task refers to another scene")
    require(task["scene_revision"] == scene["revision"], "Task refers to another revision")
    if task["status"] == "succeeded":
        require(result is not None, "Succeeded task requires a supplied result")
        validate_result(result, scene)
        require(task["result_id"] == result["result_id"], "Task result ID mismatch")
        require(task["source"] == result["source"], "Task and result source mismatch")


def check_document_links() -> int:
    paths = list(ROOT.glob("*.md"))
    for directory in ("docs", "apps", "packages", "contracts", "examples", ".github"):
        paths.extend((ROOT / directory).rglob("*.md"))
    count = 0
    for path in paths:
        for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            target = target.strip("<>")
            if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            local = unquote(target.split("#", 1)[0])
            require((path.parent / local).exists(), f"Broken link in {path.relative_to(ROOT)}: {target}")
            count += 1
    return count


def check_traceability() -> int:
    trace = load_json(ROOT / "docs/traceability.json")
    expected = {f"REQ-{n:02d}" for n in range(1, 8)} | {f"NFR-{n:02d}" for n in range(1, 5)}
    ids = [item["requirement_id"] for item in trace["items"]]
    require(len(ids) == len(set(ids)) and set(ids) == expected, "Missing or duplicate requirements")
    definitions = (ROOT / "docs/verification.md").read_text(encoding="utf-8")
    requirements = (ROOT / "docs/requirements.md").read_text(encoding="utf-8")
    for item in trace["items"]:
        require(item["requirement_id"] in requirements, "Requirement not defined")
        require(bool(item["verification_ids"]), "Missing verification plan")
        require(item["status"] in ("planned", "in_progress", "verified"), "Unknown requirement status")
        for case in item["verification_ids"]:
            require(f"### {case} " in definitions, f"Undefined verification: {case}")
        for evidence in item["evidence"]:
            require((ROOT / evidence).is_file(), f"Missing evidence: {evidence}")
        if item["status"] == "verified":
            require(bool(item["evidence"]), "Verified requirement must reference evidence")
    return len(ids)


def check_sources() -> int:
    manifest = load_json(ROOT / "docs/references/manifest.json")
    for entry in manifest["documents"]:
        original = ROOT / "docs/references" / entry["file"]
        require(original.is_file(), f"Missing source: {original.name}")
        require(hashlib.sha256(original.read_bytes()).hexdigest() == entry["sha256"], f"Source changed: {original.name}")
        require((ROOT / "docs/references" / entry["text_extract"]).is_file(), "Missing text extract")
    return len(manifest["documents"])


def main() -> int:
    scene = load_json(ROOT / "examples/scene.mock.json")
    result = load_json(ROOT / "examples/field.mock.json")
    task = load_json(ROOT / "examples/task.mock.json")
    validate_scene(scene)
    validate_result(result, scene)
    validate_task(task, scene, result)
    sys.path.insert(0, str(ROOT / "packages/analysis"))
    from crowdturb_analysis.core import analyze, check_schema
    trajectory = load_json(ROOT / "examples/trajectory.mock.json")
    analysis = load_json(ROOT / "examples/analysis.mock.json")
    derived = load_json(ROOT / "examples/field-from-trajectory.mock.json")
    expected_field, expected_analysis = analyze(trajectory, analysis["threshold"]["value"], analysis["threshold"]["basis"])
    require(analysis == expected_analysis and derived == expected_field, "Generated trajectory fixtures are stale")
    check_schema(derived, "derived-field")
    print("PASS: 3 draft schemas and linked mock fixtures")
    print("PASS: trajectory, derived-field and analysis proposals with reproducible mock aggregation")
    print(f"PASS: {check_document_links()} local document links")
    print(f"PASS: {check_traceability()} requirement trace entries (not product acceptance)")
    print(f"PASS: {check_sources()} original document checksums")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
