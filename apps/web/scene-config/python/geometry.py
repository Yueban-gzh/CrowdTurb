# -*- coding: utf-8 -*-
"""A 模块几何工具：旋转矩形、包含/重叠/距离判定与栅格掩码。

只依赖 numpy，避免给 A 模块引入 shapely/scipy 等额外依赖。
所有多边形均为简单多边形，顶点按顺序给出（顺/逆时针均可）。
"""

from __future__ import annotations

import math

import numpy as np

EPS = 1e-9


# ---------------------------------------------------------------- 基础构造

def rect_polygon(x: float, y: float, w: float, h: float):
    """轴对齐矩形 → 多边形（逆时针）。"""
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def facility_polygon(center, length: float, width: float, rotation_deg: float):
    """参数化矩形设施 → 世界坐标多边形。

    length 沿局部 x 轴，width 沿局部 y 轴，rotation_deg 为绕中心逆时针角度。
    """
    cx, cy = float(center[0]), float(center[1])
    hl, hw = float(length) / 2.0, float(width) / 2.0
    th = math.radians(float(rotation_deg))
    c, s = math.cos(th), math.sin(th)
    local = [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]
    return [(cx + lx * c - ly * s, cy + lx * s + ly * c) for lx, ly in local]


def polygon_area(poly) -> float:
    """鞋带公式，返回绝对面积。"""
    s = 0.0
    n = len(poly)
    for k in range(n):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


# ---------------------------------------------------------------- 点 / 线段 / 多边形

def point_on_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float,
                     tol: float = EPS) -> bool:
    cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
    if abs(cross) > tol * max(1.0, math.hypot(x2 - x1, y2 - y1)):
        return False
    return (min(x1, x2) - tol <= px <= max(x1, x2) + tol and
            min(y1, y2) - tol <= py <= max(y1, y2) + tol)


def point_in_polygon(px: float, py: float, poly, include_boundary: bool = True) -> bool:
    """射线法。include_boundary=True 时边界点也算在内。"""
    if include_boundary:
        n = len(poly)
        for k in range(n):
            x1, y1 = poly[k]
            x2, y2 = poly[(k + 1) % n]
            if point_on_segment(px, py, x1, y1, x2, y2):
                return True
    inside = False
    n = len(poly)
    for k in range(n):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % n]
        if (y1 > py) != (y2 > py):
            xin = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < xin:
                inside = not inside
    return inside


def point_strictly_inside_polygon(px: float, py: float, poly) -> bool:
    """严格在内部：边界上的点返回 False。"""
    n = len(poly)
    for k in range(n):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % n]
        if point_on_segment(px, py, x1, y1, x2, y2):
            return False
    return point_in_polygon(px, py, poly, include_boundary=False)


def polygon_mask(XX: np.ndarray, YY: np.ndarray, poly) -> np.ndarray:
    """向量化点在多边形内判定，用于格心采样。"""
    inside = np.zeros(XX.shape, dtype=bool)
    n = len(poly)
    for k in range(n):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % n]
        if y1 == y2:
            continue
        cond = (YY >= min(y1, y2)) & (YY < max(y1, y2))
        xin = x1 + (YY - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cond & (XX < xin)
    return inside


def rotated_rect_mask(XX: np.ndarray, YY: np.ndarray, center, length: float,
                      width: float, rotation_deg: float, tol: float = EPS) -> np.ndarray:
    """旋转矩形格心掩码：格心落在矩形内（含边界）即不可通行。"""
    cx, cy = float(center[0]), float(center[1])
    th = math.radians(float(rotation_deg))
    c, s = math.cos(th), math.sin(th)
    dx, dy = XX - cx, YY - cy
    lx = dx * c + dy * s
    ly = -dx * s + dy * c
    return (np.abs(lx) <= float(length) / 2.0 + tol) & \
           (np.abs(ly) <= float(width) / 2.0 + tol)


# ---------------------------------------------------------------- 相交 / 包含

def _project(poly, ax: float, ay: float):
    vals = [x * ax + y * ay for x, y in poly]
    return min(vals), max(vals)


def convex_polygons_overlap(a, b, tol: float = 1e-9) -> bool:
    """分离轴定理：两个凸多边形是否有正面积重叠。仅接触不算重叠。"""
    for poly in (a, b):
        n = len(poly)
        for k in range(n):
            x1, y1 = poly[k]
            x2, y2 = poly[(k + 1) % n]
            ax, ay = -(y2 - y1), (x2 - x1)
            norm = math.hypot(ax, ay)
            if norm < EPS:
                continue
            ax, ay = ax / norm, ay / norm
            amin, amax = _project(a, ax, ay)
            bmin, bmax = _project(b, ax, ay)
            if amax <= bmin + tol or bmax <= amin + tol:
                return False
    return True


def _orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def segments_properly_intersect(p1, p2, q1, q2, tol: float = EPS) -> bool:
    """两条线段是否在内部真正交叉（端点相接/共线重叠不算）。"""
    o1 = _orient(p1[0], p1[1], p2[0], p2[1], q1[0], q1[1])
    o2 = _orient(p1[0], p1[1], p2[0], p2[1], q2[0], q2[1])
    o3 = _orient(q1[0], q1[1], q2[0], q2[1], p1[0], p1[1])
    o4 = _orient(q1[0], q1[1], q2[0], q2[1], p2[0], p2[1])
    return ((o1 > tol and o2 < -tol) or (o1 < -tol and o2 > tol)) and \
           ((o3 > tol and o4 < -tol) or (o3 < -tol and o4 > tol))


def segment_intersects_polygon_interior(p1, p2, poly, tol: float = EPS) -> bool:
    """线段是否进入多边形内部（端点落在边界上不算）。"""
    if point_strictly_inside_polygon(p1[0], p1[1], poly):
        return True
    if point_strictly_inside_polygon(p2[0], p2[1], poly):
        return True
    n = len(poly)
    for k in range(n):
        q1, q2 = poly[k], poly[(k + 1) % n]
        if segments_properly_intersect(p1, p2, q1, q2, tol):
            return True
    return False


def polygon_inside_polygon(inner, outer, tol: float = EPS) -> bool:
    """inner 是否完整落在 outer 内（允许边界接触）。"""
    for p in inner:
        if not point_in_polygon(p[0], p[1], outer, include_boundary=True):
            return False
    ni, no = len(inner), len(outer)
    for i in range(ni):
        p1, p2 = inner[i], inner[(i + 1) % ni]
        for j in range(no):
            q1, q2 = outer[j], outer[(j + 1) % no]
            if segments_properly_intersect(p1, p2, q1, q2, tol):
                return False
    # outer 的凹口顶点可能落在 inner 内部而没有边交叉
    for p in outer:
        if point_strictly_inside_polygon(p[0], p[1], inner):
            return False
    # 共线边界：检查 inner 每条边向内的邻域是否仍在 outer 内
    for i in range(ni):
        p1, p2 = inner[i], inner[(i + 1) % ni]
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        norm = math.hypot(dx, dy)
        if norm < EPS:
            continue
        mx, my = (p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0
        nx, ny = -dy / norm, dx / norm
        eps = 1e-6
        for sign in (1.0, -1.0):
            qx, qy = mx + sign * eps * nx, my + sign * eps * ny
            if point_strictly_inside_polygon(qx, qy, inner):
                if not point_in_polygon(qx, qy, outer, include_boundary=True):
                    return False
                break
    return True


def polygon_inside_rect(poly, x0: float, y0: float, x1: float, y1: float,
                        tol: float = EPS) -> bool:
    return all(x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol for x, y in poly)


# ---------------------------------------------------------------- 距离

def point_segment_distance(px: float, py: float, x1: float, y1: float,
                           x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    if l2 <= EPS:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / l2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def point_polygon_distance(px: float, py: float, poly) -> float:
    if point_in_polygon(px, py, poly, include_boundary=True):
        return 0.0
    n = len(poly)
    return min(point_segment_distance(px, py, poly[k][0], poly[k][1],
                                      poly[(k + 1) % n][0], poly[(k + 1) % n][1])
               for k in range(n))


def segment_segment_distance(p1, p2, q1, q2) -> float:
    if segments_properly_intersect(p1, p2, q1, q2):
        return 0.0
    return min(point_segment_distance(p1[0], p1[1], q1[0], q1[1], q2[0], q2[1]),
               point_segment_distance(p2[0], p2[1], q1[0], q1[1], q2[0], q2[1]),
               point_segment_distance(q1[0], q1[1], p1[0], p1[1], p2[0], p2[1]),
               point_segment_distance(q2[0], q2[1], p1[0], p1[1], p2[0], p2[1]))


def segment_polygon_distance(p1, p2, poly) -> float:
    """线段到多边形的最近距离；相交为 0。"""
    if segment_intersects_polygon_interior(p1, p2, poly):
        return 0.0
    best = min(point_polygon_distance(p1[0], p1[1], poly),
               point_polygon_distance(p2[0], p2[1], poly))
    n = len(poly)
    for k in range(n):
        best = min(best, segment_segment_distance(p1, p2, poly[k], poly[(k + 1) % n]))
    return best
