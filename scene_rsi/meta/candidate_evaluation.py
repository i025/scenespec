"""Trusted post-execution scoring; never loads or calls a candidate agent."""
from __future__ import annotations

import json
import time

from ..evaluator.verifier import SceneVerifier
from .candidate_runner import RunLimits, parse_spec, parse_state


def evaluate_execution(spec_json: bytes, execution: dict) -> dict:
    """Evaluate a blocked run or labelled offline capture outside any candidate.

    Live results are deliberately unsupported until a backend is accepted. This
    prevents a fixture or a forged 'verified' report from becoming live evidence.
    """
    if (execution.get("mode") not in {"blocked", "offline_fixture"}
            or execution.get("execution_started") is not False
            or execution.get("limits_enforced") is not False):
        raise ValueError("only blocked results and explicit offline fixtures are currently supported")
    limits = RunLimits(**execution["limits"])
    spec = parse_spec(spec_json, limits)
    report = {"mode": execution["mode"], "execution": execution,
              "dynamic_execution_passed": None, "verdict": None,
              "verifier": "trusted_original_SceneVerifier", "verifier_wall_seconds": None,
              "scene_generation_improved": None, "is_rsi": False}
    if execution["mode"] == "blocked":
        report["status"] = "not_evaluated"
        return report
    if execution["status"] != "state_received":
        report.update(status="fixture_transport_failure",
                      verdict={"valid": False, "scores": None,
                               "failures": [{"type": execution["status"]}]})
        return report
    # Ignore any candidate-supplied valid/scores claims. Rebuild only plain state
    # data and let the original verifier decide every hard scene constraint.
    state = parse_state(json.dumps(execution["state"], allow_nan=False).encode(), limits)
    started = time.monotonic()
    report["verdict"] = SceneVerifier(grid_step=0.1).verify(spec, state)
    report["verifier_wall_seconds"] = time.monotonic() - started
    report["status"] = "fixture_scored"
    return report
