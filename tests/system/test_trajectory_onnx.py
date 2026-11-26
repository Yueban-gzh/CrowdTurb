import importlib.util
import unittest

from apps.api.crowdturb.trajectory_mock import SimulationInput


@unittest.skipUnless(importlib.util.find_spec("numpy"), "requires NumPy")
class TrajectoryTests(unittest.TestCase):
    def test_visible_exit_uses_diagonal_and_corner_shortcut_is_blocked(self):
        import numpy as np
        from apps.api.crowdturb.trajectory_onnx import GridRoutes, simulate

        request = SimulationInput(
            result_id="line-test", scene_id="line-test", revision=1, input_hash="0" * 64,
            scene={"exits": [{"id": "E1", "side": "S"}], "params": {"walk_speed": 1.2}},
            grid={"nx": 5, "ny": 5, "dx": 1.0, "dy": 1.0, "origin": [0, 0]},
            walkable=[[True] * 5 for _ in range(5)],
            exit_ids=("E1",), exit_cells={"E1": [(3, 0), (4, 0)]},
            initial_agents=[{"agent_id": "p", "position_m": [0.5, 3.5], "velocity_mps": [0.0, 0.0]}],
            seed=0, horizon_s=10, step_s=0.08,
        )
        routes = GridRoutes(request)
        start = np.array([0.5, 3.5])
        self.assertEqual(len(routes.reference_route(start)), 2)
        self.assertTrue(np.array_equal(routes.target(np.array([3.8, 3.5])), [3.8, 0.5]))

        def inference(state, history, target, speed):
            return np.zeros((len(state), 2))

        result = simulate(request, inference, "1" * 64, "test", routes=routes)
        x, y = result["frames"][1]["agents"][0]["position_m"]
        self.assertGreater(x, start[0])
        self.assertLess(y, start[1])
        self.assertEqual(len(result["exit_events"]), 1)

        request.walkable[2][1] = False
        self.assertFalse(GridRoutes(request).visible(start, np.array([2.5, 1.5])))

    def test_neighbors_exclude_self_and_people_behind(self):
        import numpy as np
        from apps.api.crowdturb.trajectory_onnx import neighbors

        state = np.array([[1, 0, 1, 0, 0, 0], [2, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0]])
        indices, visible = neighbors(state)
        self.assertEqual(indices[0, visible[0]].tolist(), [1])
        self.assertFalse(visible[1:].any())

    def test_blocked_person_can_yield_without_reducing_the_gap(self):
        import numpy as np
        from apps.api.crowdturb.trajectory_onnx import GAP_M, GridRoutes

        routes = GridRoutes(SimulationInput(
            result_id="yield-test", scene_id="yield-test", revision=1, input_hash="0" * 64,
            scene={"exits": [{"id": "E1", "side": "S"}]},
            grid={"nx": 3, "ny": 3, "dx": 0.5, "dy": 0.5, "origin": [0, 0]},
            walkable=[[False, True, False], [True, True, True], [True, True, True]],
            exit_ids=("E1",), exit_cells={"E1": [(1, 0)]},
            initial_agents=[], seed=0, horizon_s=1, step_s=0.08,
        ))
        positions = np.array([[0.75, 0.80], [0.75, 0.55]])
        velocities = np.array([[0.0, -1.2], [0.0, 1.2]])
        moved, active, exits = routes.advance(positions, velocities, velocities, 1.2, 0.08)

        self.assertTrue(active.all())
        self.assertEqual(exits, [])
        self.assertTrue(np.any(moved[:, 0] != positions[:, 0]))
        self.assertGreaterEqual(np.linalg.norm(moved[0] - moved[1]), GAP_M)

    def test_routes_around_wall_and_records_only_the_configured_exit(self):
        import numpy as np
        from apps.api.crowdturb.trajectory_onnx import GridRoutes, simulate

        request = SimulationInput(
            result_id="route-test", scene_id="route-test", revision=1, input_hash="0" * 64,
            scene={"exits": [{"id": "E1", "side": "E"}], "params": {"walk_speed": 1.2}},
            grid={"nx": 4, "ny": 3, "dx": 1.0, "dy": 1.0, "origin": [0, 0]},
            walkable=[[True, False, True, True], [True, False, True, True], [True, True, True, True]],
            exit_ids=("E1",), exit_cells={"E1": [(3, 0)]},
            initial_agents=[{"agent_id": "p", "position_m": [0.5, 0.5], "velocity_mps": [0.0, 0.0]}],
            seed=0, horizon_s=15, step_s=0.08,
        )
        routes = GridRoutes(request)
        blocked, event = routes.sweep_axis(np.array([0.5, 0.5]), 2.0, 0)
        self.assertLess(blocked[0], 1.0)
        self.assertIsNone(event)
        blocked, event = routes.sweep_axis(np.array([3.8, 1.5]), 0.5, 0)
        self.assertLess(blocked[0], 4)
        self.assertIsNone(event)

        def inference(state, history, target, speed):
            return np.zeros((len(state), 2))

        result = simulate(request, inference, "1" * 64, "test")
        positions = [agent["position_m"] for frame in result["frames"] for agent in frame["agents"]]
        self.assertTrue(all(routes.open(routes.cell(np.array(position))) for position in positions))
        self.assertTrue(any(y > 2 and x > 1 for x, y in positions))
        self.assertEqual([(event["agent_id"], event["exit_id"]) for event in result["exit_events"]], [("p", "E1")])
        self.assertEqual(result["frames"][-1], {"time_s": 15, "agents": []})
