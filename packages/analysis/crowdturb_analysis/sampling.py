"""Build deterministic coarse samples from declared ranges, current values and budget."""

import math
import numpy as np

from .candidates import generate_candidates
from scene_io import variable_parameters


def auto_candidates(scene, parameters=None, *, points_per_parameter=3, max_candidates=100):
    if (not isinstance(points_per_parameter, int) or isinstance(points_per_parameter, bool)
            or not 3 <= points_per_parameter <= 101):
        raise ValueError("points_per_parameter must be an integer from 3 to 101")
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or max_candidates < 1:
        raise ValueError("Candidate budget must be a positive integer")
    declared = variable_parameters(scene)
    if parameters is None:
        chosen = declared
    else:
        if not isinstance(parameters, list) or not parameters or any(
                not isinstance(p, dict) or set(p) != {"type", "target_id"} for p in parameters):
            raise ValueError("Parameters must list type and target_id bindings")
        bindings = [(p["type"], p["target_id"]) for p in parameters]
        if len(set(bindings)) != len(bindings):
            raise ValueError("Duplicate parameter binding")
        by_binding = {(d["type"], d["target_id"]): d for d in declared}
        if any(b not in by_binding for b in bindings):
            raise ValueError("Unknown parameter binding")
        chosen = [by_binding[b] for b in bindings]
    if not chosen:
        raise ValueError("No declared parameters to sample")
    samples, protected = [], []
    for parameter in chosen:
        low, high = parameter["allowed_range"]
        current = parameter["current_value"]
        anchors = set((float(low), float(current), float(high)))
        values = sorted(anchors | set(float(v) for v in np.linspace(low, high, points_per_parameter)))
        while len(values) > max(points_per_parameter, len(anchors)):
            removable = [v for v in values if v not in anchors]
            # Remove the most redundant interior sample; deterministic tie break.
            value = min(removable, key=lambda v: (min(abs(v - u) for u in values if u != v), v))
            values.remove(value)
        samples.append({"type": parameter["type"], "target_id": parameter["target_id"], "values": values})
        protected.append(anchors)
    minimum = math.prod(len(p) for p in protected)
    if minimum > max_candidates:
        raise ValueError(f"Preserving endpoints/current needs at least {minimum} combinations; increase budget or select fewer parameters")
    while math.prod(len(s["values"]) for s in samples) > max_candidates:
        options = [i for i, s in enumerate(samples) if any(v not in protected[i] for v in s["values"])]
        i = max(options, key=lambda i: (len(samples[i]["values"]), -i))
        values = samples[i]["values"]
        removable = [v for v in values if v not in protected[i]]
        value = min(removable, key=lambda v: (min(abs(v - u) for u in values if u != v), v))
        values.remove(value)
    report = generate_candidates(scene, samples, max_candidates)
    report["search"].update({"method": "auto_coarse_grid", "points_requested": points_per_parameter,
                             "samples": samples, "anchors_preserved": True,
                             "note": "Endpoints and current values preserved; interior samples reduced to fit budget. Geometry dedup follows."})
    return report
