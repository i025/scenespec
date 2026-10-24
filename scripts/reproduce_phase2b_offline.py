"""Record the P2B fail-closed path and synthetic transport checks, never execute candidates."""
from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scene_rsi.meta.fixed import FixedMetaConfig, _parent, prepare_request, record_candidate
from scene_rsi.meta.proposals import _read_regular
from scene_rsi.meta.candidate_environment import inspect_candidate_environment
from scene_rsi.meta.candidate_runner import CandidateRunner, RunLimits, load_audited_candidate, offline_capture
from scene_rsi.meta.candidate_evaluation import evaluate_execution


def save(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path, help="existing Phase 1 dataset; dev export only")
    parser.add_argument("--out", required=True, type=Path, help="new controller-only output directory")
    args = parser.parse_args()
    dataset, out = args.dataset.resolve(), args.out.resolve()
    if out.is_relative_to(ROOT) or out.is_relative_to(dataset) or dataset.is_relative_to(out):
        parser.error("output must be outside the repository and separate from the dataset")
    parent = _parent(ROOT)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    save(out / "environment.json", inspect_candidate_environment())
    prepare_request(ROOT, dataset, out / "request", config=FixedMetaConfig(max_candidates=1))
    agent = "scene_rsi/agent/fixed_agent.py"
    before = (ROOT / agent).read_text()
    patch = f"diff --git a/{agent} b/{agent}\n" + "".join(difflib.unified_diff(
        before.splitlines(keepends=True), (before + "\n# Offline runner fixture; no algorithm change.\n").splitlines(keepends=True),
        fromfile="a/" + agent, tofile="b/" + agent))
    (out / "fixture.patch").write_text(patch)
    audited = record_candidate(ROOT, out / "request", out / "fixture.patch", out / "audit")
    assert audited["status"] == "accepted_static_only", audited
    candidate = load_audited_candidate(Path(audited["record_path"]).parent)
    spec_bytes = _read_regular(out / "request/tasks/dev_00000.json", 1048576)
    blocked = CandidateRunner().run(candidate, spec_bytes)
    assert blocked["status"] == "blocked" and not blocked["execution_started"]
    save(out / "candidate-execution.json", evaluate_execution(spec_bytes, blocked))

    # Tiny independent codec fixtures, never benchmark tasks, reference files,
    # generated programs or evidence of an algorithm repair.
    spec = {"scene_id": "synthetic-transport", "room": {"width": 4, "depth": 4, "height": 3},
            "assets": {"box": {"width": 1, "depth": 1, "height": 1}},
            "required_objects": [{"id": "box_1", "asset": "box"}]}
    state = {"scene_id": spec["scene_id"], "objects": [
        {"id": "box_1", "asset": "box", "x": 2, "y": 2, "yaw_deg": 0}]}
    fixtures = out / "fixtures"
    fixtures.mkdir(mode=0o700)
    save(fixtures / "spec.json", spec)
    save(fixtures / "state.json", state)
    spec_json, state_json = json.dumps(spec).encode(), json.dumps(state).encode()
    captures = {
        "valid_state": offline_capture(state_json),
        "missing_object": offline_capture(json.dumps({**state, "objects": []}).encode()),
        "timeout": offline_capture(b"", termination="timeout"),
        "pid_limit": offline_capture(b"", termination="pid_limit"),
        "memory_limit": offline_capture(b"", termination="memory_limit"),
        "nonzero_exit": offline_capture(state_json, exit_code=1),
        "output_limit": offline_capture(b"x" * 1025, limits=RunLimits(stdout_bytes=1024)),
    }
    checks = []
    for name, capture in captures.items():
        report = evaluate_execution(spec_json, capture)
        save(fixtures / f"{name}.json", report)
        checks.append({"name": name, "status": capture["status"], "fixture_valid": report["verdict"]["valid"]})
    assert checks[0]["fixture_valid"] and not any(row["fixture_valid"] for row in checks[1:])
    assert _parent(ROOT) == parent
    summary = {
        "scope": "P2B interface and offline fallback only; Phase 2B not completed",
        "code_baseline": "5c39ebf", "source_git": parent,
        "static_audit": {"status": audited["status"], "record": audited["record_path"],
                         "candidate_git": audited["candidate_git"]},
        "dynamic_execution": {"status": "blocked", "reason": blocked["reason"],
                              "candidate_processes_started": 0, "acceptance_passed": None},
        "comparison": {
            "status": "not_run", "reason": "no_verified_isolation_backend",
            "baseline": {"dev_success_rate": None, "val_success_rate": None, "runtime_seconds": None, "resources": None},
            "repair": {"dev_success_rate": None, "val_success_rate": None, "runtime_seconds": None, "resources": None},
            "error_type_comparison": None, "scene_generation_improved": None,
        },
        "offline_checks": checks, "offline_checks_are_algorithm_comparison": False,
        "live_security_regressions": "not_run", "val_tasks_read": False, "test_split_used": False,
        "candidate_resource_metrics": blocked["resources"], "actual_model_calls": 0,
        "gpu_accessed": False, "is_rsi": False,
        "unimplemented": ["administrator-approved execution service", "verified isolation adapter",
                          "live boundary and malicious-behavior acceptance", "human algorithm-repair execution",
                          "dev/val dynamic comparison", "live Codex proposals"],
    }
    save(out / "summary.json", summary)
    print(json.dumps({"summary": str(out / "summary.json"), "status": "blocked",
                      "candidate_processes_started": 0, "offline_checks": len(checks)}, indent=2))


if __name__ == "__main__":
    main()
