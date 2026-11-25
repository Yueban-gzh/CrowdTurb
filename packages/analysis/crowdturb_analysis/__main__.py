"""Local D entry point, independent of E's future task service."""

import argparse
import json
from pathlib import Path
from jsonschema import ValidationError

from .core import analyze
from .candidates import generate_candidates
from .sampling import auto_candidates
from .comparison import compare_candidates
from .demo import build_demo
from scene_io import load_scene


def read(path):
    def reject(value):
        raise ValueError(f"Non-finite JSON constant: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="CrowdTurb D analysis and candidate preparation")
    commands = parser.add_subparsers(dest="command", required=True)
    metrics = commands.add_parser("analyze")
    metrics.add_argument("trajectory")
    metrics.add_argument("--threshold", type=float, required=True)
    metrics.add_argument("--threshold-basis", required=True)
    metrics.add_argument("--out", required=True)
    metrics.add_argument("--field-out", required=True)
    metrics.add_argument("--density-method", choices=["cell", "gaussian_visible"], default="cell")
    metrics.add_argument("--bandwidth-m", type=float)
    search = commands.add_parser("candidates")
    search.add_argument("scene")
    search.add_argument("samples")
    search.add_argument("--budget", type=int, default=100)
    search.add_argument("--out", required=True)
    automatic = commands.add_parser("auto-candidates")
    automatic.add_argument("scene")
    automatic.add_argument("--parameters", help="Optional JSON list of type/target_id bindings")
    automatic.add_argument("--points", type=int, default=3)
    automatic.add_argument("--budget", type=int, default=100)
    automatic.add_argument("--out", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("scene")
    compare.add_argument("candidates")
    compare.add_argument("baseline")
    compare.add_argument("runs", help="JSON object mapping candidate ID to trajectory or explicit failure")
    compare.add_argument("policy")
    compare.add_argument("--threshold", type=float, required=True)
    compare.add_argument("--threshold-basis", required=True)
    compare.add_argument("--density-method", choices=["cell", "gaussian_visible"], default="cell")
    compare.add_argument("--bandwidth-m", type=float)
    compare.add_argument("--out", required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    try:
        if args.command == "analyze":
            paths = [Path(p).resolve() for p in (args.trajectory, args.out, args.field_out)]
            if len(set(paths)) != 3:
                raise ValueError("Trajectory input and the two outputs must have different paths")
            field, result = analyze(read(args.trajectory), args.threshold, args.threshold_basis,
                                    density_method=args.density_method, bandwidth_m=args.bandwidth_m)
            write(args.out, result)
            write(args.field_out, field)
            print(f"PASS: {len(field['frames'])} frames; source={result['source']}; output={args.out}")
        elif args.command == "candidates":
            if Path(args.out).resolve() in {Path(args.scene).resolve(), Path(args.samples).resolve()}:
                raise ValueError("Candidate output cannot overwrite scene or samples")
            report = generate_candidates(load_scene(args.scene), read(args.samples), args.budget)
            write(args.out, report)
            print(f"PASS: {report['candidate_count']} candidates; {report['unique_solve_count']} unique feasible inputs; NOT simulated")
        elif args.command == "auto-candidates":
            inputs = [args.scene] + ([args.parameters] if args.parameters else [])
            if Path(args.out).resolve() in {Path(p).resolve() for p in inputs}:
                raise ValueError("Candidate output cannot overwrite input")
            report = auto_candidates(load_scene(args.scene), read(args.parameters) if args.parameters else None,
                                     points_per_parameter=args.points, max_candidates=args.budget)
            write(args.out, report)
            print(f"PASS: {report['candidate_count']} auto combinations; {report['unique_solve_count']} unique feasible inputs; NOT simulated")
        elif args.command == "compare":
            inputs = (args.scene, args.candidates, args.baseline, args.runs, args.policy)
            if Path(args.out).resolve() in {Path(p).resolve() for p in inputs}:
                raise ValueError("Comparison output cannot overwrite input")
            report = compare_candidates(load_scene(args.scene), read(args.candidates), read(args.baseline),
                                        read(args.runs), read(args.policy), args.threshold, args.threshold_basis,
                                        density_method=args.density_method, bandwidth_m=args.bandwidth_m)
            write(args.out, report)
            print(f"PASS: compared {len(report['candidates'])} candidate records; source={report['source']}; selected={report['recommendation']}")
        else:
            from .core import REPO
            output_dir = Path(args.out_dir)
            payload = build_demo(load_scene(REPO / "apps/web/scene-config/scenes/passage_optimization_example.json"))
            if output_dir.resolve() == (REPO / "examples").resolve():
                raise ValueError("Demo output must not overwrite curated examples")
            for name, value in payload.items():
                write(output_dir / (name + ".mock.json"), value)
            print(f"PASS: SYNTHETIC DEMO ONLY; source=mock; {len(payload['comparison']['candidates'])} candidates; no simulator called; output={output_dir}")
    except (ValueError, OSError, ValidationError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")


if __name__ == "__main__":
    main()
