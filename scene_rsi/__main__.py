"""CLI: python -m scene_rsi generate|validate|benchmark ..."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from .schema import SceneSpec, SceneState
from .agent.fixed_agent import FixedSceneAgent
from .evaluator.verifier import SceneVerifier
from .export import export_obj, export_svg


def main() -> None:
    parser = argparse.ArgumentParser(description="Scene-RSI V0: deterministic baseline and trusted validator")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="generate and evaluate one scene")
    generate.add_argument("--spec", required=True)
    generate.add_argument("--out", required=True, help="output directory")
    validate = commands.add_parser("validate", help="evaluate an existing SceneState independently")
    validate.add_argument("--spec", required=True)
    validate.add_argument("--state", required=True)
    bench = commands.add_parser("benchmark", help="evaluate *.json scene specs in a directory")
    bench.add_argument("--spec-dir", required=True)
    bench.add_argument("--out", required=True)
    args = parser.parse_args()

    verifier = SceneVerifier()
    if args.command == "generate":
        spec = SceneSpec.load(args.spec)
        state = FixedSceneAgent().generate(spec)
        result = verifier.verify(spec, state)
        out = Path(args.out)
        out.mkdir(exist_ok=True, parents=True)
        state.save(out / "scene_state.json")
        (out / "evaluation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        export_svg(spec, state, out / "floorplan.svg")
        export_obj(spec, state, out / "scene.obj")
        print(json.dumps({"valid": result["valid"], "scores": result["scores"],
                          "failures": result["failures"], "files": str(out)}, indent=2))
    elif args.command == "validate":
        result = verifier.verify(SceneSpec.load(args.spec), SceneState.load(args.state))
        print(json.dumps(result, indent=2))
    else:
        source = Path(args.spec_dir)
        filenames = sorted(source.glob("*.json"))
        if not filenames:
            parser.error("no *.json SceneSpecs found")
        details = []
        for filename in filenames:
            spec = SceneSpec.load(filename)
            state = FixedSceneAgent().generate(spec)
            details.append(verifier.verify(spec, state))
        summary = {"n": len(details), "n_valid": sum(row["valid"] for row in details),
                   "valid_rate": round(sum(row["valid"] for row in details) / len(details), 4),
                   "average_diagnostic_score": round(sum(row["scores"]["mean"] for row in details) / len(details), 4),
                   "results": details}
        out = Path(args.out)
        out.parent.mkdir(exist_ok=True, parents=True)
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))

if __name__ == "__main__":
    main()
