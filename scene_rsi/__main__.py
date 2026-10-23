"""Trusted baseline, reproducible benchmark, and offline patch audit CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from .schema import SceneSpec, SceneState
from .evaluator.verifier import SceneVerifier
from .export import export_obj, export_svg
from .integrity import require_original_contract


def main() -> None:
    parser = argparse.ArgumentParser(description="Scene-RSI phase 1: CPU baseline, benchmark and fixed meta interface")
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
    dataset = commands.add_parser("make-dataset", help="construct verified feasible dev/val/test tasks")
    dataset.add_argument("--out", required=True, help="new controller-only directory")
    dataset.add_argument("--seed", type=int, default=20261023)
    dataset.add_argument("--per-family", type=int, default=4)
    evaluate = commands.add_parser("evaluate", help="evaluate the trusted fixed baseline on one dataset split")
    evaluate.add_argument("--dataset", required=True)
    evaluate.add_argument("--split", choices=("dev", "val", "test"), required=True)
    evaluate.add_argument("--out", required=True)
    evaluate.add_argument("--final-test", action="store_true", help="consume the held-out test reservation")
    evaluate.add_argument("--frozen-commit", help="full clean HEAD commit required for final test")
    prepare = commands.add_parser("meta-prepare", help="export a dev-only fixed meta request; makes no model call")
    prepare.add_argument("--repo", default=".")
    prepare.add_argument("--dataset", required=True)
    prepare.add_argument("--out", required=True)
    prepare.add_argument("--dev-report")
    prepare.add_argument("--model", help="pin a concrete model identifier for the future invocation plan")
    prepare.add_argument("--max-candidates", type=int, default=8)
    plan = commands.add_parser("meta-plan", help="print a deployment plan; never launches Codex")
    plan.add_argument("--bundle", required=True)
    record = commands.add_parser("meta-record", help="archive and statically check a patch; never executes it")
    record.add_argument("--repo", default=".")
    record.add_argument("--bundle", required=True)
    record.add_argument("--patch", required=True)
    record.add_argument("--out", required=True)
    commands.add_parser("check-integrity", help="compare evaluator/schema/original tests against attachment hashes")
    args = parser.parse_args()

    try:
        require_original_contract()
        if args.command == "check-integrity":
            print(json.dumps(require_original_contract(), indent=2))
            return
        if args.command == "make-dataset":
            from .benchmark.dataset import build_dataset
            build_dataset(Path(args.out), seed=args.seed, per_family=args.per_family)
            print(json.dumps({"dataset": str(Path(args.out).resolve()),
                              "manifest": str(Path(args.out) / "manifest.json")}, indent=2))
            return
        if args.command == "evaluate":
            from .benchmark.runner import run_dataset
            result = run_dataset(Path(args.dataset), args.split, Path(args.out),
                                 final_test=args.final_test, frozen_commit=args.frozen_commit)
            print(json.dumps({k: result[k] for k in ("n", "n_valid", "valid_rate")}, indent=2))
            return
        if args.command == "meta-prepare":
            from .meta import FixedMetaConfig, prepare_request
            result = prepare_request(Path(args.repo), Path(args.dataset), Path(args.out),
                                     dev_report=Path(args.dev_report) if args.dev_report else None,
                                     config=FixedMetaConfig(model=args.model, max_candidates=args.max_candidates))
            print(json.dumps(result, indent=2))
            return
        if args.command == "meta-plan":
            from .meta import proposal_plan
            print(json.dumps(proposal_plan(Path(args.bundle)), indent=2))
            return
        if args.command == "meta-record":
            from .meta import record_candidate
            result = record_candidate(Path(args.repo), Path(args.bundle), Path(args.patch), Path(args.out))
            print(json.dumps(result, indent=2))
            if result["status"] != "accepted_static_only":
                raise SystemExit(1)
            return
        if args.command == "benchmark":
            from .benchmark.runner import run_specs
            result = run_specs(sorted(Path(args.spec_dir).glob("*.json")), Path(args.out))
            print(json.dumps({k: result[k] for k in ("n", "n_valid", "valid_rate",
                                                   "average_diagnostic_score")}, indent=2))
            return
        if args.command in {"generate", "validate"}:
            from .benchmark.runner import reject_managed_dataset_input
            reject_managed_dataset_input(Path(args.spec))
            if args.command == "validate":
                reject_managed_dataset_input(Path(args.state))
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")

    verifier = SceneVerifier()
    if args.command == "generate":
        from .agent.fixed_agent import FixedSceneAgent
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
if __name__ == "__main__":
    main()
