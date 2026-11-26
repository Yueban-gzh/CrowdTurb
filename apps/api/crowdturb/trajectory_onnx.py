"""Single-floor ONNX inference with grid routing and sampled trajectories."""

import hashlib
import json
import math
from heapq import heapify, heappop, heappush

import numpy as np

STEP_S = 0.08
MAX_PEOPLE = 100
MAX_HORIZON_S = 120
MAX_FRAME_INTERVALS = 120
GAP_M = 0.25
DIRECTIONS = ((0, -1), (-1, 0), (1, 0), (0, 1))
DIAGONALS = ((-1, -1), (1, -1), (-1, 1), (1, 1))
PLANNER_VERSION = "eight-way-visible-route-v2"
EXIT_DIRECTION = {"S": (0, -1), "W": (-1, 0), "E": (1, 0), "N": (0, 1)}
YIELD_SPEED_FRACTION = 0.35


def _digest(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def neighbors(state):
    velocity = state[:, 2:4]
    relative = state[None, :, :2] - state[:, None, :2]
    distance = np.linalg.norm(relative, axis=2)
    forward = np.einsum("ijk,ik->ij", relative, velocity)
    visible = (distance <= 4.0) & (forward >= 0) & (np.linalg.norm(velocity, axis=1)[:, None] > 1e-7)
    np.fill_diagonal(visible, False)
    ranked = np.argsort(np.where(visible, distance, np.inf), axis=1, kind="stable")[:, :6]
    slots = ranked.shape[1]
    indices = np.zeros((len(state), 6), dtype=np.int64)
    mask = np.zeros_like(indices, dtype=bool)
    mask[:, :slots] = np.take_along_axis(visible, ranked, axis=1)
    indices[:, :slots] = np.where(mask[:, :slots], ranked, 0)
    return indices, mask


class STDDN:
    def __init__(self, model_path):
        import onnxruntime as ort

        weights = model_path.read_bytes()
        self.model_sha256 = hashlib.sha256(weights).hexdigest()
        self.runtime_version = ort.__version__
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        try:
            self.session = ort.InferenceSession(weights, sess_options=options, providers=["CPUExecutionProvider"])
        except Exception as error:
            raise ValueError(f"无法加载 STDDN 模型：{error}") from error

    def infer(self, state, history, target, speed):
        state = state.astype(np.float32)
        indices, mask = neighbors(state)
        try:
            acceleration, = self.session.run(["acceleration"], {
                "state": state[None],
                "history": history.astype(np.float32)[None],
                "target": target.astype(np.float32)[None],
                "desired_speed": np.full((1, len(state), 1), speed, dtype=np.float32),
                "neighbor_index": indices[None],
                "neighbor_mask": mask[None],
            })
        except Exception as error:
            raise ValueError(f"STDDN 单步推理失败：{error}") from error
        if acceleration.shape != (1, len(state), 2) or acceleration.dtype != np.float32:
            raise ValueError("模型输出应为 float32 [1, N, 2]。")
        if not np.isfinite(acceleration).all():
            raise ValueError("模型输出包含非有限加速度。")
        return acceleration[0].astype(float)

    def __call__(self, request, *, routes=None):
        return simulate(request, self.infer, self.model_sha256, self.runtime_version, routes=routes)

    def simulate_with_reference(self, request):
        routes = GridRoutes(request)
        trajectory = self(request, routes=routes)
        agents = trajectory["frames"][0]["agents"]
        if not agents:
            return trajectory, None
        center = np.mean([agent["position_m"] for agent in agents], axis=0)
        representative = min(agents, key=lambda agent: np.linalg.norm(agent["position_m"] - center))
        points = routes.reference_route(np.array(representative["position_m"]))
        return trajectory, {"points_m": points} if len(points) > 1 else None


class GridRoutes:
    def __init__(self, request):
        self.grid = request.grid
        self.origin = np.array(request.grid["origin"], dtype=float)
        self.spacing = np.array([request.grid["dx"], request.grid["dy"]], dtype=float)
        self.size = (request.grid["nx"], request.grid["ny"])
        self.walkable = request.walkable
        self.gates = {}
        sides = {item["id"]: item["side"] for item in request.scene["exits"]}
        for exit_id in request.exit_ids:
            for coordinates in request.exit_cells[exit_id]:
                cell = tuple(coordinates)
                if self.open(cell):
                    self.gates.setdefault(cell, (exit_id, EXIT_DIRECTION[sides[exit_id]]))
        if not self.gates:
            raise ValueError("场景没有可通行的出口格。")
        self.next_cell = {cell: cell for cell in self.gates}
        self.distance = dict.fromkeys(self.gates, 0)
        self.shortcuts = {}
        self.openings = self.exit_openings()
        moves = [(dx, dy, math.hypot(dx * self.spacing[0], dy * self.spacing[1]))
                 for dx, dy in DIRECTIONS + DIAGONALS]
        queue = [(0, cell) for cell in self.gates]
        heapify(queue)
        while queue:
            cost, cell = heappop(queue)
            if cost != self.distance[cell]:
                continue
            for dx, dy, length in moves:
                adjacent = (cell[0] + dx, cell[1] + dy)
                if not self.open(adjacent):
                    continue
                if dx and dy and not (self.open((cell[0] + dx, cell[1])) and self.open((cell[0], cell[1] + dy))):
                    continue
                distance = cost + length
                if distance < self.distance.get(adjacent, math.inf):
                    self.next_cell[adjacent] = cell
                    self.distance[adjacent] = distance
                    heappush(queue, (distance, adjacent))

    def exit_openings(self):
        groups = {}
        for cell, (exit_id, direction) in self.gates.items():
            axis = 0 if direction[0] else 1
            groups.setdefault((exit_id, axis, cell[axis]), []).append(cell[1 - axis])
        openings = []
        for (_, axis, edge), indices in groups.items():
            start = previous = min(indices)
            for index in sorted(indices)[1:] + [math.inf]:
                if index != previous + 1:
                    openings.append((axis, edge, start, previous))
                    start = index
                previous = index
        return openings

    def open(self, cell):
        ix, iy = cell
        return 0 <= ix < self.size[0] and 0 <= iy < self.size[1] and self.walkable[iy][ix]

    def cell(self, position):
        return tuple(np.floor((position - self.origin) / self.spacing).astype(int))

    def center(self, cell):
        return self.origin + (np.array(cell) + 0.5) * self.spacing

    def visible(self, start, end):
        """Walk every crossed cell, including both side cells at a corner."""
        x, y = ((start - self.origin) / self.spacing).tolist()
        end_x, end_y = ((end - self.origin) / self.spacing).tolist()
        ix, iy = math.floor(x), math.floor(y)
        final = (math.floor(end_x), math.floor(end_y))
        if not self.open((ix, iy)) or not self.open(final):
            return False
        dx, dy = end_x - x, end_y - y
        sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        tx = (ix + (sx > 0) - x) / dx if dx else math.inf
        ty = (iy + (sy > 0) - y) / dy if dy else math.inf
        step_x = abs(1 / dx) if dx else math.inf
        step_y = abs(1 / dy) if dy else math.inf
        while (ix, iy) != final:
            # A segment ending on a cell edge can already be in the final cell on one axis.
            cross_x = tx if ix != final[0] else math.inf
            cross_y = ty if iy != final[1] else math.inf
            if abs(cross_x - cross_y) <= 1e-10:
                if not (self.open((ix + sx, iy)) and self.open((ix, iy + sy))):
                    return False
                ix, iy = ix + sx, iy + sy
                tx, ty = tx + step_x, ty + step_y
            elif cross_x < cross_y:
                ix, tx = ix + sx, tx + step_x
            else:
                iy, ty = iy + sy, ty + step_y
            if not self.open((ix, iy)):
                return False
        return True

    def target(self, position):
        cell = self.cell(position)
        if cell in self.gates:
            return position + np.array(self.gates[cell][1]) * self.spacing
        endpoints = []
        for axis, edge, start, end in self.openings:
            lateral = 1 - axis
            point = position.copy()
            point[axis] = self.origin[axis] + (edge + 0.5) * self.spacing[axis]
            inset = min(GAP_M / 2, self.spacing[lateral] / 4)
            low = self.origin[lateral] + start * self.spacing[lateral] + inset
            high = self.origin[lateral] + (end + 1) * self.spacing[lateral] - inset
            point[lateral] = min(max(position[lateral], low), high)
            endpoints.append(point)
        for point in sorted(endpoints, key=lambda point: np.linalg.norm(point - position)):
            if self.visible(position, point):
                return point
        if cell not in self.shortcuts:
            start = self.center(cell)
            node = self.next_cell[cell]
            while node not in self.gates:
                farther = self.next_cell[node]
                if not self.visible(start, self.center(farther)):
                    break
                node = farther
            self.shortcuts[cell] = self.center(node)
        point = self.shortcuts[cell]
        return point if self.visible(position, point) else self.center(self.next_cell[cell])

    def reference_route(self, position):
        points = [position.tolist()]
        for _ in self.next_cell:
            if self.cell(position) in self.gates:
                return points
            position = self.target(position)
            points.append(position.tolist())
        raise ValueError("无法生成参考路线。")

    def sweep_axis(self, start, displacement, axis):
        end = start.copy()
        end[axis] += displacement
        cell = self.cell(start)
        last = self.cell(end)[axis]
        if cell[axis] == last:
            return end, None
        sign = 1 if last > cell[axis] else -1
        direction = tuple(sign if coordinate == axis else 0 for coordinate in range(2))
        previous = cell
        for index in range(cell[axis] + sign, last + sign, sign):
            crossed = list(cell)
            crossed[axis] = index
            crossed = tuple(crossed)
            if not self.open(crossed):
                if index < 0 or index >= self.size[axis]:
                    gate = self.gates.get(previous)
                    if gate and gate[1] == direction:
                        return end, gate[0]
                edge = self.origin[axis] + max(previous[axis], index) * self.spacing[axis]
                end[axis] = edge - sign * min(1e-6, self.spacing[axis] / 10)
                return end, None
            previous = crossed
        return end, None

    def clear_move(self, start, displacement, axis, others):
        end, exit_id = self.sweep_axis(start, displacement, axis)
        differences = others - start
        old_gap = np.linalg.norm(differences, axis=1)
        movement = end[axis] - start[axis]
        differences[:, axis] -= np.clip(differences[:, axis], min(0, movement), max(0, movement))
        if np.any(np.linalg.norm(differences, axis=1) + 1e-8 < np.minimum(old_gap, GAP_M)):
            return start, None
        return end, exit_id

    def advance(self, positions, velocities, desired, speed, step):
        accepted = positions.copy()
        remaining = np.ones(len(positions), dtype=bool)
        exits = []
        # Let the front of each queue move before the people behind it.
        order = sorted(range(len(positions)), key=lambda index: self.distance[self.cell(positions[index])])
        for index in order:
            velocity = velocities[index]
            others = remaining.copy()
            others[index] = False
            nearby = accepted[others]
            for axis in np.argsort(-np.abs(velocity), kind="stable"):
                accepted[index], exit_id = self.clear_move(accepted[index], velocity[axis] * step, axis, nearby)
                if exit_id:
                    exits.append((index, exit_id))
                    remaining[index] = False
                    break
            progress = np.linalg.norm(accepted[index] - positions[index])
            if not remaining[index] or progress >= speed * step * 0.05:
                continue
            # A blocked person may take a short step sideways to make space.
            lateral_axis = int(np.argmin(np.abs(desired[index])))
            sidesteps = [self.clear_move(accepted[index], sign * speed * step * YIELD_SPEED_FRACTION,
                                         lateral_axis, nearby) for sign in (-1, 1)]
            end, exit_id = max(sidesteps, key=lambda move: np.linalg.norm(move[0] - accepted[index]))
            accepted[index] = end
            if exit_id:
                exits.append((index, exit_id))
                remaining[index] = False
        return accepted, remaining, exits


def simulate(request, inference, model_sha256, runtime_version, *, routes=None):
    if not math.isclose(request.step_s, STEP_S, rel_tol=0, abs_tol=1e-9):
        raise ValueError("此 STDDN 配置需要 0.08 秒的推理步长。")
    people = len(request.initial_agents)
    if people > MAX_PEOPLE:
        raise ValueError(f"当前场景有 {people} 人，ONNX 评估最多支持 {MAX_PEOPLE} 人。请在布局编辑的人群区里减少人数后重试。")
    if not 0 < request.horizon_s <= MAX_HORIZON_S:
        raise ValueError(f"ONNX 评估时长必须大于 0 且不超过 {MAX_HORIZON_S} 秒。")
    speed = float(request.scene["params"]["walk_speed"])
    if not 0 < speed <= 3:
        raise ValueError("STDDN 首版的期望速度需在 0 至 3 m/s 之间，且大于 0。")
    if routes is None:
        routes = GridRoutes(request)
    initial = sorted(request.initial_agents, key=lambda agent: agent["agent_id"])
    ids = np.array([agent["agent_id"] for agent in initial])
    state = np.array([agent["position_m"] + agent["velocity_mps"] + [0.0, 0.0] for agent in initial]).reshape(-1, 6)
    if any(routes.cell(position) not in routes.next_cell for position in state[:, :2]):
        raise ValueError("至少有一名初始人员所在的网格无法到达出口。")
    history = np.repeat(state[:, None, :], 8, axis=1)

    def snapshot():
        return [{"agent_id": agent_id, "position_m": row[:2].tolist(), "velocity_mps": row[2:4].tolist()}
                for agent_id, row in zip(ids.tolist(), state)]

    first = snapshot()
    frames = [{"time_s": 0.0, "agents": first}]
    events = []
    steps = max(1, math.ceil(request.horizon_s / STEP_S - 1e-10))
    stride = max(1, math.ceil(steps / MAX_FRAME_INTERVALS))
    time = 0.0
    for step_number in range(1, steps + 1):
        next_time = request.horizon_s if step_number == steps else step_number * STEP_S
        duration = next_time - time
        if len(state):
            targets = np.stack([routes.target(position) for position in state[:, :2]])
            difference = targets - state[:, :2]
            desired = difference * (speed / np.maximum(np.linalg.norm(difference, axis=1), 1e-8))[:, None]
            model_acceleration = inference(state, history, targets, speed)
            acceleration = 0.4 * np.clip(model_acceleration, -4, 4) + 0.6 * (desired - state[:, 2:4]) / 0.35
            velocity = state[:, 2:4] + acceleration * duration
            velocity *= np.minimum(1, (speed * 1.5) / np.maximum(np.linalg.norm(velocity, axis=1), 1e-8))[:, None]
            positions, active, exits = routes.advance(state[:, :2], velocity, desired, speed, duration)
            events.extend({"agent_id": str(ids[index]), "exit_id": exit_id, "time_s": next_time}
                          for index, exit_id in exits)
            actual_velocity = (positions - state[:, :2]) / duration
            actual_acceleration = (actual_velocity - state[:, 2:4]) / duration
            state = np.column_stack((positions, actual_velocity, actual_acceleration))[active]
            ids = ids[active]
            history = np.concatenate((history[active, 1:], state[:, None, :]), axis=1)
        if step_number % stride == 0 or step_number == steps:
            frames.append({"time_s": next_time, "agents": snapshot()})
        time = next_time

    return {
        "schema_version": "0.2.0",
        "result_id": request.result_id,
        "scene_id": request.scene_id,
        "scene_revision": request.revision,
        "input_hash": request.input_hash,
        "initial_state_hash": _digest(first),
        "behavior_hash": _digest({"speed_mps": speed, "gap_m": GAP_M, "controller": "stddn-blend-v3"}),
        "source": "stddn",
        "population_mode": "fixed_closed",
        "model_sha256": model_sha256,
        "reproducibility_record": f"onnxruntime={runtime_version}; CPUExecutionProvider; sampling_stride={stride}",
        "simulation_settings": {"dt_s": STEP_S, "seed": request.seed, "integrator": "stddn-blend-euler-v1",
                                "planner_version": PLANNER_VERSION, "collision_version": "grid-sweep-yield-v2"},
        "grid": request.grid,
        "walkable": request.walkable,
        "exit_ids": list(request.exit_ids),
        "frames": frames,
        "exit_events": events,
    }
