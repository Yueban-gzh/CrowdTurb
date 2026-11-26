"""Import D's first-round Mock samples without changing their provenance."""

from jsonschema import Draft202012Validator

from .repository import load_json

SAMPLES = {
    "playback": {
        "trajectory": "trajectory.mock.json",
        "derived-field": "field-from-trajectory.mock.json",
        "analysis": "analysis.mock.json",
    },
    "comparison": {"comparison": "comparison.mock.json"},
}


def load_review_sample(root, kind):
    artifacts = {}
    for name, filename in SAMPLES[kind].items():
        payload = load_json(root / "examples" / filename)
        schema = load_json(root / "contracts" / f"{name}.schema.json")
        Draft202012Validator(schema).validate(payload)
        if payload["source"] != "mock":
            raise ValueError(f"{filename} 的来源必须是 mock。")
        artifacts[name] = payload

    if kind == "comparison":
        baseline = artifacts["comparison"]
        return {
            "scene_id": baseline["scene_id"],
            "scene_revision": None,
            "input_hash": baseline["baseline_input_hash"],
            "baseline_result_id": baseline["baseline_result_id"],
        }, artifacts

    trajectory = artifacts["trajectory"]
    identity = {
        key: trajectory[key] for key in ("scene_id", "scene_revision", "input_hash")
    }
    for name in ("derived-field", "analysis"):
        artifact = artifacts[name]
        if any(artifact[key] != value for key, value in identity.items()):
            raise ValueError(f"{name} 的场景版本或输入哈希与轨迹不一致。")
        if artifact["derived_from"] != trajectory["result_id"]:
            raise ValueError(f"{name} 引用了不同的轨迹。")
        if artifact["grid"] != trajectory["grid"]:
            raise ValueError(f"{name} 的网格与轨迹不一致。")
    field = artifacts["derived-field"]
    if field["walkable"] != trajectory["walkable"]:
        raise ValueError("聚合场与轨迹的可行走掩码不一致。")
    frame_times = [frame["time_s"] for frame in trajectory["frames"]]
    if frame_times != [frame["time_s"] for frame in field["frames"]]:
        raise ValueError("聚合场与轨迹的时间帧不一致。")
    if artifacts["analysis"]["time_range_s"] != [frame_times[0], frame_times[-1]]:
        raise ValueError("分析时间范围与轨迹不一致。")
    return identity, artifacts
