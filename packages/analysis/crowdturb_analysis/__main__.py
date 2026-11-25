"""Local D entry point, independent of E's future task service."""

import argparse
import json
from pathlib import Path
from jsonschema import ValidationError

from .core import analyze
from .candidates import generate_candidates
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
    search = commands.add_parser("candidates")
    search.add_argument("scene")
    search.add_argument("samples")
    search.add_argument("--budget", type=int, default=100)
    search.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        if args.command == "analyze":
            paths = [Path(p).resolve() for p in (args.trajectory, args.out, args.field_out)]
            if len(set(paths)) != 3:
                raise ValueError("Trajectory input and the two outputs must have different paths")
            field, result = analyze(read(args.trajectory), args.threshold, args.threshold_basis)
            write(args.out, result)
            write(args.field_out, field)
            print(f"PASS: {len(field['frames'])} frames; source={result['source']}; output={args.out}")
        else:
            if Path(args.out).resolve() in {Path(args.scene).resolve(), Path(args.samples).resolve()}:
                raise ValueError("Candidate output cannot overwrite scene or samples")
            report = generate_candidates(load_scene(args.scene), read(args.samples), args.budget)
            write(args.out, report)
            print(f"PASS: {report['candidate_count']} candidates; {report['unique_solve_count']} unique feasible inputs; NOT simulated")
    except (ValueError, OSError, ValidationError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")


if __name__ == "__main__":
    main()
