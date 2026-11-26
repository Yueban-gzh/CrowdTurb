"""Cell counting and an experimental, mass-preserving physical-bandwidth estimator.

The Gaussian is normalized per person over visible walkable cell centers, cut off
at 3 sigma. It is a numerical alternative for sensitivity studies, not calibrated
crowd density or a safety criterion.
"""

import math
import numpy as np


def estimator_config(method="cell", bandwidth_m=None):
    if method == "cell":
        if bandwidth_m is not None:
            raise ValueError("Cell estimator has no bandwidth")
        return {"method": "cell", "bandwidth_m": None, "support_sigma": None,
                "boundary": "whole_walkable_cell", "version": "1"}
    if method != "gaussian_visible":
        raise ValueError("Unknown density estimator")
    if (not isinstance(bandwidth_m, (int, float)) or isinstance(bandwidth_m, bool)
            or not math.isfinite(bandwidth_m) or bandwidth_m <= 0):
        raise ValueError("Gaussian estimator needs a finite positive bandwidth in meters")
    return {"method": method, "bandwidth_m": float(bandwidth_m), "support_sigma": 3.0,
            "boundary": "visible_walkable_centers_per_person_normalization", "version": "1"}


def visible(start, end, mask, grid):
    """Conservative grid ray: both side cells must be open at a corner crossing."""
    sx, sy = start
    ex, ey = end
    ox, oy = grid["origin"]
    dx, dy = grid["dx"], grid["dy"]
    ix, iy = math.floor((sx - ox) / dx), math.floor((sy - oy) / dy)
    tx, ty = math.floor((ex - ox) / dx), math.floor((ey - oy) / dy)
    vx, vy = ex - sx, ey - sy
    stepx, stepy = (1 if vx > 0 else -1), (1 if vy > 0 else -1)
    crossx = ((ox + (ix + (vx > 0)) * dx - sx) / vx) if vx else math.inf
    crossy = ((oy + (iy + (vy > 0)) * dy - sy) / vy) if vy else math.inf
    deltax, deltay = (dx / abs(vx) if vx else math.inf), (dy / abs(vy) if vy else math.inf)

    def open_cell(x, y):
        return 0 <= x < grid["nx"] and 0 <= y < grid["ny"] and mask[y, x]

    while (ix, iy) != (tx, ty):
        if abs(crossx - crossy) < 1e-12:
            if not open_cell(ix + stepx, iy) or not open_cell(ix, iy + stepy):
                return False
            ix, iy = ix + stepx, iy + stepy
            crossx, crossy = crossx + deltax, crossy + deltay
        elif crossx < crossy:
            ix, crossx = ix + stepx, crossx + deltax
        else:
            iy, crossy = iy + stepy, crossy + deltay
        if not open_cell(ix, iy):
            return False
    return True


def aggregate(agents, grid, walkable, config):
    ny, nx = grid["ny"], grid["nx"]
    area = grid["dx"] * grid["dy"]
    mask = np.asarray(walkable, dtype=bool)
    mass = np.zeros((ny, nx), dtype=float)
    momentum = np.zeros((ny, nx, 2), dtype=float)
    for agent in agents:
        x, y = agent["position_m"]
        ix = math.floor((x - grid["origin"][0]) / grid["dx"])
        iy = math.floor((y - grid["origin"][1]) / grid["dy"])
        if config["method"] == "cell":
            cells, weights = [(ix, iy)], [1.0]
        else:
            h = config["bandwidth_m"]
            radius = 3 * h
            x0 = max(0, math.floor((x - radius - grid["origin"][0]) / grid["dx"]))
            x1 = min(nx - 1, math.floor((x + radius - grid["origin"][0]) / grid["dx"]))
            y0 = max(0, math.floor((y - radius - grid["origin"][1]) / grid["dy"]))
            y1 = min(ny - 1, math.floor((y + radius - grid["origin"][1]) / grid["dy"]))
            cells, weights = [], []
            for cy in range(y0, y1 + 1):
                for cx in range(x0, x1 + 1):
                    if not mask[cy, cx]:
                        continue
                    center = (grid["origin"][0] + (cx + 0.5) * grid["dx"],
                              grid["origin"][1] + (cy + 0.5) * grid["dy"])
                    d2 = (center[0] - x)**2 + (center[1] - y)**2
                    if d2 <= radius**2 and visible((x, y), center, mask, grid):
                        cells.append((cx, cy))
                        weights.append(math.exp(-0.5 * (d2 / h / h)))
            if not cells:
                raise ValueError("Bandwidth too small to resolve visible grid centers; choose a larger physical scale")
            total = sum(weights)
            weights = [w / total for w in weights]
        for (cx, cy), weight in zip(cells, weights):
            mass[cy, cx] += weight
            momentum[cy, cx] += weight * np.asarray(agent["velocity_mps"])
    velocity = np.zeros_like(momentum)
    np.divide(momentum, mass[:, :, None], out=velocity, where=mass[:, :, None] > 0)
    return mass / area, velocity
