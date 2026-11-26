"""Generate declared parameter combinations and delegate geometry/deduplication to A."""

import copy
import itertools
import math
from pathlib import Path
import sys

SCENE_LIB = Path(__file__).resolve().parents[3] / "apps/web/scene-config/python"
sys.path.insert(0, str(SCENE_LIB))
from layout_eval import evaluate_candidates
from scene_io import validate, variable_parameters


def generate_candidates(scene, samples, max_candidates=100):
    """samples: [{type, target_id, values}]; no silent truncation of the search space."""
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or max_candidates < 1:
        raise ValueError("max_candidates must be a positive integer")
    problems = validate(scene)
    if any(p["level"] == "error" for p in problems):
        raise ValueError("Baseline scene is infeasible")
    if not isinstance(samples, list) or not samples:
        raise ValueError("Provide at least one sampled parameter")
    declarations = {(d["type"], d["target_id"]): d for d in variable_parameters(scene)}
    seen = set()
    combinations = 1
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"type", "target_id", "values"}:
            raise ValueError("Sample needs type, target_id, values")
        binding = (sample["type"], sample["target_id"])
        if binding not in declarations or binding in seen:
            raise ValueError("Unknown or duplicate sampled parameter")
        seen.add(binding)
        values = sample["values"]
        if not isinstance(values, list) or not values:
            raise ValueError("Parameter values must be a nonempty list")
        low, high = declarations[binding]["allowed_range"]
        for value in values:
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or not low <= value <= high):
                raise ValueError("Sample value outside declared finite range")
        combinations *= len(values)
        if combinations > max_candidates:
            raise ValueError(f"Search needs {combinations} combinations, exceeding budget {max_candidates}")
    candidates = []
    facilities = {f["id"]: f for f in scene["geometry"].get("facilities", [])}
    for number, values in enumerate(itertools.product(*(s["values"] for s in samples)), start=1):
        changes = {}
        for spec, value in zip(samples, values):
            kind, target = spec["type"], spec["target_id"]
            if kind in ("exit_width", "passage_width"):
                key = ("exit_id" if kind == "exit_width" else "passage_id", target)
                changes.setdefault(key, {key[0]: target})["width"] = value
            else:
                key = ("facility_id", target)
                change = changes.setdefault(key, {"facility_id": target})
                if kind in ("facility_center_x", "facility_center_y"):
                    center = change.setdefault("center", copy.deepcopy(facilities[target]["center"]))
                    center[0 if kind.endswith("_x") else 1] = value
                else:
                    field = {"facility_rotation": "rotation_deg", "facility_length": "length", "facility_width": "width"}[kind]
                    change[field] = value
        candidates.append({"candidate_id": f"D{number:04d}", "changes": list(changes.values())})
    report = evaluate_candidates(scene, candidates)
    report["search"] = {"method": "bounded_grid", "max_candidates": max_candidates, "combinations": combinations,
                        "evaluation_status": "not_evaluated", "note": "Geometry only; no simulation or improvement claim"}
    return report
