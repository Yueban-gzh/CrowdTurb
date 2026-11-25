"""Axis-aligned passages with adjustable clear width inside a reserved envelope.

The two banks are generated geometry, never cuts through existing walls/obstacles.
Direction x/y is the direction of travel; width is perpendicular to that axis.
"""

import math

from geometry import EPS, rect_polygon


def check_passage(p):
    required = {"id", "name", "center", "direction", "length", "width",
                "envelope_width", "width_limits", "adjustable"}
    if not isinstance(p, dict) or set(p) != required:
        raise ValueError("通道必须包含且只包含 id/name/center/direction/length/width/"
                         "envelope_width/width_limits/adjustable")
    if not isinstance(p["id"], str) or not p["id"].strip() or not isinstance(p["name"], str):
        raise ValueError("通道 id 必须非空，name 必须是字符串")
    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if (not isinstance(p["center"], list) or len(p["center"]) != 2
            or not all(number(v) for v in p["center"])):
        raise ValueError("通道 center 必须是两个有限数")
    if p["direction"] not in ("x", "y") or not isinstance(p["adjustable"], bool):
        raise ValueError("通道 direction 必须是 x/y，adjustable 必须为布尔值")
    for key in ("length", "width", "envelope_width"):
        if not number(p[key]) or p[key] <= 0:
            raise ValueError(f"通道 {key} 必须是有限正数")
    limits = p["width_limits"]
    if (not isinstance(limits, list) or len(limits) != 2 or not all(number(v) for v in limits)
            or not (0 < limits[0] <= p["width"] <= limits[1] <= p["envelope_width"])):
        raise ValueError("通道宽度必须满足 0 < min <= width <= max <= envelope_width")


def passage_envelope(p):
    x, y = p["center"]
    w, h = ((p["length"], p["envelope_width"]) if p["direction"] == "x"
            else (p["envelope_width"], p["length"]))
    return rect_polygon(x - w / 2, y - h / 2, w, h)


def passage_intersects_wall(p, segment):
    """Clip a segment against the open envelope, including boundary-to-boundary crossings."""
    poly = passage_envelope(p)
    low = [min(v[i] for v in poly) + EPS for i in (0, 1)]
    high = [max(v[i] for v in poly) - EPS for i in (0, 1)]
    start, end = segment[:2], segment[2:]
    t0, t1 = 0.0, 1.0
    for i in (0, 1):
        delta = end[i] - start[i]
        if abs(delta) < EPS:
            if not low[i] <= start[i] <= high[i]:
                return False
        else:
            a, b = (low[i] - start[i]) / delta, (high[i] - start[i]) / delta
            t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
            if t0 > t1:
                return False
    return True


def passage_banks(p):
    """Return the two solid banks as fixed rectangular obstacles (possibly empty)."""
    thickness = (p["envelope_width"] - p["width"]) / 2
    if thickness <= EPS:
        return []
    x, y = p["center"]
    if p["direction"] == "x":
        boxes = [(x - p["length"] / 2, y - p["envelope_width"] / 2, p["length"], thickness),
                 (x - p["length"] / 2, y + p["width"] / 2, p["length"], thickness)]
    else:
        boxes = [(x - p["envelope_width"] / 2, y - p["length"] / 2, thickness, p["length"]),
                 (x + p["width"] / 2, y - p["length"] / 2, thickness, p["length"])]
    return [{"name": f"通道 {p['id']} 隔挡 {i + 1}", "x": a, "y": b, "w": w, "h": h}
            for i, (a, b, w, h) in enumerate(boxes)]
