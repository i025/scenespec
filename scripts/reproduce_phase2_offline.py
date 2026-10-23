"""Reproduce the authorized P2A fallback; no model or candidate execution.

Use from the repository root with Python 3.10+. The existing dataset is read
only through prepare_request's dev loader. Synthetic outputs are labelled.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scene_rsi.integrity import require_original_contract
from scene_rsi.meta import FixedMetaConfig, prepare_request
from scene_rsi.meta.capabilities import probe_capabilities
from scene_rsi.meta.fixed import _parent
from scene_rsi.meta.proposals import ingest_offline_proposal, request_proposal, safe_plan


def save(path: Path, data) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def fixture(repo_path: str, suffix: str) -> str:
    before = (ROOT / repo_path).read_text(encoding="utf-8")
    return (f"diff --git a/{repo_path} b/{repo_path}\n" + "".join(difflib.unified_diff(
        before.splitlines(keepends=True), (before + suffix).splitlines(keepends=True),
        fromfile="a/" + repo_path, tofile="b/" + repo_path)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--namespace-probe", action="store_true")
    args = parser.parse_args()
    dataset, out = args.dataset.resolve(), args.out.resolve()
    if out.is_relative_to(ROOT) or out.is_relative_to(dataset) or dataset.is_relative_to(out):
        parser.error("output must be outside the repository and separate from the dataset")
    parent = _parent(ROOT)
    integrity = require_original_contract()
    manifest_hash = hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    with (out / "tests.log").open("x", encoding="utf-8") as log:
        completed = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=180, check=False)
    if completed.returncode:
        raise RuntimeError(f"trusted unit tests failed; see {out / 'tests.log'}")
    capabilities = probe_capabilities(namespace_probe=args.namespace_probe)
    save(out / "capabilities.json", capabilities)
    save(out / "integrity.json", integrity)
    request = prepare_request(ROOT, dataset, out / "request",
                              config=FixedMetaConfig(model="offline-fixture-model", max_candidates=3))
    save(out / "plan.json", safe_plan(ROOT, out / "request"))
    fixture_dir = out / "fixtures"
    fixture_dir.mkdir(mode=0o700)
    rationale = "Synthetic controller fixture for offline audit testing only; no improvement claim."
    save(fixture_dir / "accepted.json", {
        "patch": fixture("scene_rsi/agent/fixed_agent.py", "\n# Offline audit fixture; algorithm unchanged.\n"),
        "rationale": rationale,
    })
    save(fixture_dir / "protected.json", {
        "patch": fixture("scene_rsi/evaluator/verifier.py", "\n# Protected-path rejection fixture.\n"),
        "rationale": rationale,
    })
    events = fixture_dir / "synthetic-events.jsonl"
    events.write_text(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 11, "cached_input_tokens": 3, "output_tokens": 7,
    }}) + "\n", encoding="utf-8")
    accepted = ingest_offline_proposal(ROOT, out / "request", fixture_dir / "accepted.json",
                                       out / "audit", events=events)
    rejected = ingest_offline_proposal(ROOT, out / "request", fixture_dir / "protected.json", out / "audit")
    blocked = request_proposal(ROOT, out / "request", out / "audit")
    exhausted = request_proposal(ROOT, out / "request", out / "audit")
    assert accepted["status"] == "accepted_static_only", accepted
    assert rejected["status"] == "rejected" and rejected["candidate_record"]["checks"][-1]["name"] == "patch_scope", rejected
    assert blocked["status"] == "blocked" and blocked["reason"] == "missing_verified_backend", blocked
    assert exhausted["reason"] == "candidate_budget_exhausted", exhausted
    results = [accepted, rejected, blocked, exhausted]
    assert all(r["resources"]["model_calls"] == 0 and r["execution"]["status"] == "not_run" for r in results)
    assert _parent(ROOT) == parent
    assert hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest() == manifest_hash
    summary = {
        "scope": "P2A safety design and offline tests only", "source_git": parent,
        "baseline_commit": "f116002d464f5ca8c6e529ea58b9d223ac4ab47c",
        "request_id": request["request_id"], "model": request["config"]["model"],
        "model_identifier_note": "placeholder used only for synthetic offline fixtures; no real model selected",
        "dataset_manifest_sha256": manifest_hash, "dataset_manifest_unchanged": True,
        "tests": {"status": "passed", "log": "tests.log"},
        "evidence": {"static_audit_passed": True, "isolated_execution_passed": None,
                     "dev_success_rate_improved": None, "val_success_rate_improved": None,
                     "rsi_evidence": False},
        "actual_model_calls": 0, "actual_model_tokens": None, "actual_model_cost": None,
        "dynamic_candidate_tests": "not_run", "test_split": "not_used",
        "comparison": "No new candidate scores; improvement cannot be measured without isolated execution.",
        "attempts": [{"candidate_id": r["candidate_id"], "attempt_index": r["attempt_index"],
                      "source": r["source"], "status": r["status"], "reason": r["reason"],
                      "proposal_record_path": r["proposal_record_path"], "resources": r["resources"]}
                     for r in results],
        "runtime_regressions": "not_run; malicious code fixtures remained inert strings",
        "unimplemented": ["verified isolation backend", "credential-isolated model broker",
                          "live Codex proposals", "candidate execution", "dev/val candidate scoring",
                          "version selection and promotion"],
    }
    save(out / "summary.json", summary)
    print(json.dumps({"summary": str(out / "summary.json"), "actual_model_calls": 0,
                      "candidate_execution": "not_run", "offline_attempt_statuses": [r["status"] for r in results]}, indent=2))


if __name__ == "__main__":
    main()
