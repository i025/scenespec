"""Trusted, CPU-only baseline evaluation and transparent task-level accounting.

This module never imports a candidate module, accepts a code path, or executes a
patch. It is not a sandbox for generated code. The fixed baseline has no enforced
per-task timeout; a future isolated candidate runner must supply that boundary.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from math import sqrt
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Iterable

from ..agent.fixed_agent import FixedSceneAgent
from ..evaluator.verifier import SceneVerifier
from ..schema import PlacedObject, SceneSpec, SceneState


SOURCE_ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = (
    "scene_rsi/schema.py", "scene_rsi/geometry.py",
    "scene_rsi/evaluator/verifier.py", "scene_rsi/agent/fixed_agent.py",
    "scene_rsi/benchmark/dataset.py",
    "scene_rsi/benchmark/runner.py",
)
REPORT_VERSION = 1


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reject_managed_dataset_input(path: str | Path) -> None:
    """Keep legacy commands from bypassing managed-split evaluation controls.

    Any ancestor manifest marks managed input, even if that manifest is corrupt.
    This is an operator guard, not a boundary against copying files elsewhere.
    Resolving the input also catches symlinks into managed task/witness trees.
    """
    resolved = Path(path).resolve()
    directory = resolved if resolved.is_dir() else resolved.parent
    for ancestor in (directory, *directory.parents):
        manifest = ancestor / "manifest.json"
        if manifest.exists() or manifest.is_symlink():
            raise ValueError("managed dataset input is not allowed in legacy commands; use evaluate with an explicit split")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_provenance(root: str | Path = SOURCE_ROOT) -> dict[str, Any]:
    """Record absence of Git explicitly; ordinary baseline runs still work."""
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            check=True, timeout=10,
        )
        return result.stdout.strip()
    try:
        status = git("status", "--porcelain", "--untracked-files=normal")
        return {"commit": git("rev-parse", "HEAD"), "dirty": bool(status),
                "status": status.splitlines(), "available": True}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"commit": None, "dirty": None, "status": [],
                "available": False, "error": type(exc).__name__}


def provenance() -> dict[str, Any]:
    hashes = {name: sha256_file(SOURCE_ROOT / name) for name in SOURCE_FILES
              if (SOURCE_ROOT / name).is_file()}
    return {
        "git": git_provenance(), "source_sha256": hashes,
        "evaluator_sha256": hashes["scene_rsi/evaluator/verifier.py"],
        "python": sys.version, "python_executable": sys.executable,
        "platform": platform.platform(),
        "agent": "scene_rsi.agent.fixed_agent.FixedSceneAgent",
        "evaluator": "scene_rsi.evaluator.verifier.SceneVerifier",
        "evaluator_grid_step": 0.1,
        "execution": {"mode": "trusted_fixed_baseline_only", "gpu_used": False,
                      "per_task_timeout_enforced": False,
                      "candidate_execution_supported": False},
    }


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _resource_sample(wall_start: float, cpu_start: float) -> dict[str, Any]:
    return {"wall_seconds": time.perf_counter() - wall_start,
            "cpu_seconds": time.process_time() - cpu_start,
            "process_peak_rss_bytes": _peak_rss_bytes(),
            "rss_scope": "process lifetime high-water mark, not per-task allocation",
            "gpu_seconds": 0, "model_calls": 0, "tokens": 0}


def _state_contract(state: Any) -> None:
    """Reject malformed Python outputs; semantic checks remain in the verifier."""
    if not isinstance(state, SceneState) or not isinstance(state.scene_id, str):
        raise TypeError("agent must return a SceneState with a string scene_id")
    if not isinstance(state.objects, (tuple, list)):
        raise TypeError("SceneState.objects must be a sequence")
    for obj in state.objects:
        if not isinstance(obj, PlacedObject):
            raise TypeError("SceneState.objects must contain PlacedObject values")
        if not isinstance(obj.id, str) or not isinstance(obj.asset, str):
            raise TypeError("placed object IDs and asset IDs must be strings")
        if not all(isinstance(v, (float, int)) for v in (obj.x, obj.y, obj.yaw_deg)):
            raise TypeError("placed object transforms must contain numeric values")


def _evaluate_task(task: Any) -> dict[str, Any]:
    spec = task.spec
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    row: dict[str, Any] = {
        "scene_id": spec.scene_id, "task_id": task.task_id,
        "family": task.family, "spec_sha256": task.spec_sha256,
        "valid": False, "scores": None, "failures": [],
        "constraint_failures": [],
        "complexity": {"objects": len(spec.required_objects),
                       "constraints": len(spec.constraints),
                       "access_paths": len(spec.access_paths),
                       "regions": len(spec.regions)},
    }
    stage = "generate"
    try:
        # SceneSpec is frozen but its asset dictionary is mutable. Give the
        # agent a copy so evaluation always uses the original trusted catalog.
        state = FixedSceneAgent().generate(deepcopy(spec))
        stage = "state_contract"
        _state_contract(state)
        stage = "verify"
        verdict = SceneVerifier(grid_step=0.1).verify(spec, state)
        row.update(verdict)
        row["status"] = "success" if verdict["valid"] else "verification_failure"
        for failure in verdict["failures"]:
            if failure["type"] == "constraint_violation":
                index = failure["constraint_index"]
                row["constraint_failures"].append({
                    "constraint_index": index,
                    "constraint_type": spec.constraints[index].type,
                    "failure": dict(failure),
                })
    except Exception as exc:
        if stage == "generate":
            status = "agent_timeout" if isinstance(exc, TimeoutError) else "agent_exception"
        else:
            status = "invalid_state" if stage == "state_contract" else "verifier_exception"
        row.update(status=status, error={"type": type(exc).__name__, "message": str(exc)},
                   failures=[{"type": status, "message": str(exc)}])
    row["stage"] = stage
    row["resources"] = _resource_sample(wall_start, cpu_start)
    return row


def _wilson(n_valid: int, n: int) -> dict[str, Any]:
    z = 1.959963984540054
    p = n_valid / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    radius = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return {"lower": max(0.0, centre - radius), "upper": min(1.0, centre + radius),
            "confidence": 0.95, "method": "Wilson score",
            "assumption": "task IID approximation; shared families/templates may correlate tasks"}


def _group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n, n_valid = len(rows), sum(bool(row["valid"]) for row in rows)
    n_errors = sum(row["status"] not in {"success", "verification_failure"} for row in rows)
    return {"n": n, "n_valid": n_valid, "n_failed": n - n_valid,
            "n_errors": n_errors, "success_rate": n_valid / n,
            "success_rate_wilson95": _wilson(n_valid, n)}


def aggregate_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """All supplied tasks, including execution errors, stay in the denominator."""
    if not rows:
        raise ValueError("cannot evaluate an empty task collection")
    families: dict[str, list] = defaultdict(list)
    complexities: dict[str, list] = defaultdict(list)
    failures, failing_tasks, constraint_failures, constraint_tasks = Counter(), Counter(), Counter(), Counter()
    for row in rows:
        families[row["family"]].append(row)
        c = row["complexity"]
        key = f"objects={c['objects']}|constraints={c['constraints']}|paths={c['access_paths']}"
        complexities[key].append(row)
        kinds = [failure["type"] for failure in row["failures"]]
        failures.update(kinds)
        failing_tasks.update(set(kinds))
        subtypes = [f["constraint_type"] for f in row["constraint_failures"]]
        constraint_failures.update(subtypes)
        constraint_tasks.update(set(subtypes))
    stats = _group_stats(rows)
    by_family = {family: _group_stats(group) for family, group in sorted(families.items())}
    scores = [row["scores"]["mean"] for row in rows if row["scores"] is not None]
    return {
        **stats, "valid_rate": stats["success_rate"],
        "micro_success_rate": stats["success_rate"],
        "macro_family_success_rate": sum(v["success_rate"] for v in by_family.values()) / len(by_family),
        "by_family": by_family,
        "by_complexity": {key: _group_stats(group) for key, group in sorted(complexities.items())},
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "failure_type_counts": dict(sorted(failures.items())),
        "failure_type_task_counts": dict(sorted(failing_tasks.items())),
        "constraint_type_failure_counts": dict(sorted(constraint_failures.items())),
        "constraint_type_failed_task_counts": dict(sorted(constraint_tasks.items())),
        "average_diagnostic_score": sum(scores) / len(scores) if scores else None,
        "diagnostic_score_n": len(scores),
        "diagnostic_score_note": "unchanged verifier mean, only for completed verification; not a success metric",
    }


def evaluate_tasks(tasks: Iterable[Any], *, split: str = "legacy",
                   frozen_commit: str | None = None) -> dict[str, Any]:
    """Evaluate trusted fixed baseline only; no pluggable candidate execution."""
    tasks = list(tasks)
    if not tasks:
        raise ValueError("cannot evaluate an empty task collection")
    ids = [task.task_id for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task IDs would distort the benchmark denominator")
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    started = _utc_now()
    before = provenance()
    if frozen_commit is not None:
        _require_frozen_git(before["git"], frozen_commit)
    rows = [_evaluate_task(task) for task in tasks]
    after = provenance()
    if frozen_commit is not None:
        _require_frozen_git(after["git"], frozen_commit)
    return {"report_version": REPORT_VERSION, "split": split,
            "started_at_utc": started, "finished_at_utc": _utc_now(),
            "provenance": before,
            "source_unchanged_during_run": before["source_sha256"] == after["source_sha256"],
            "git_unchanged_during_run": before["git"] == after["git"],
            **aggregate_results(rows), "resources": _resource_sample(wall_start, cpu_start),
            "results": rows}


def _write_json(path: Path, value: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    # Write a sibling and atomically replace so interrupted evaluation cannot
    # leave a partial report that looks complete.
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _require_frozen_git(git: dict[str, Any], frozen_commit: str) -> None:
    if git.get("available") is not True or git.get("dirty") is not False or frozen_commit != git.get("commit"):
        raise ValueError("final test requires clean source Git HEAD matching frozen_commit")


def _reserve_final_test(root: Path, out: Path, frozen_commit: str | None,
                        manifest_sha256: str) -> tuple[Path, dict[str, Any]]:
    if not frozen_commit:
        raise ValueError("final test requires an explicitly frozen full Git commit")
    _require_frozen_git(git_provenance(), frozen_commit)
    private = root / "private"
    result_root = (private / "test_results").resolve()
    if not private.resolve().is_relative_to(root) or not result_root.is_relative_to(private.resolve()):
        raise ValueError("private test result directories cannot escape the dataset through symlinks")
    if not out.resolve().is_relative_to(result_root):
        raise ValueError("held-out test details must be written under dataset/private/test_results/")
    if out.exists():
        raise ValueError("final test output already exists")
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    private.chmod(0o700)
    ledger = private / "final_test_ledger.json"
    reservation = {"status": "reserved", "reserved_at_utc": _utc_now(),
                   "frozen_commit": frozen_commit,
                   "dataset_manifest_sha256": manifest_sha256,
                   "result_path": str(out.resolve()),
                   "rule": "one final evaluation per dataset; failed/interrupted reservations are consumed"}
    try:
        fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError("final test has already been reserved or evaluated for this dataset") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(reservation, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return ledger, reservation


def run_dataset(root: str | Path, split: str, out: str | Path, *,
                final_test: bool = False, frozen_commit: str | None = None) -> dict[str, Any]:
    """Run dev/val, or explicitly reserve one final baseline test evaluation.

    The ledger is an audit/control feature for the trusted operator, not an OS
    security boundary against a process with access to this whole repository.
    Generated code must never run with such access.
    """
    from .dataset import load_dataset

    if split not in {"dev", "val", "test"}:
        raise ValueError("split must be dev, val, or test")
    if split == "test" and not final_test:
        raise ValueError("held-out test requires explicit final_test=True and frozen_commit")
    if split != "test" and (final_test or frozen_commit is not None):
        raise ValueError("final-test options apply only to the test split")
    root, out = Path(root).resolve(), Path(out).resolve()
    if split != "test" and out.is_relative_to(root):
        raise ValueError("dev/val reports must be outside the dataset to preserve immutable task files")
    manifest = root / "manifest.json"
    manifest_sha256 = sha256_file(manifest)
    ledger, reservation = None, None
    if split == "test":
        ledger, reservation = _reserve_final_test(root, out, frozen_commit, manifest_sha256)
        # Recheck after reserving, before even reading the held-out tasks. If
        # another process changes source, the reservation remains consumed.
        _require_frozen_git(git_provenance(), frozen_commit)
    # Reserve final use before opening any test specification. A failure from
    # this point onwards deliberately leaves the reservation consumed.
    tasks = load_dataset(root, split)
    report = evaluate_tasks(tasks, split=split, frozen_commit=frozen_commit)
    if split == "test":
        _require_frozen_git(report["provenance"]["git"], frozen_commit)
    report["provenance"]["dataset_manifest_sha256"] = manifest_sha256
    if sha256_file(manifest) != manifest_sha256:
        raise RuntimeError("dataset manifest changed during evaluation")
    if not report["source_unchanged_during_run"]:
        raise RuntimeError("source changed during evaluation; report cannot be accepted")
    if split == "test" and not report["git_unchanged_during_run"]:
        raise RuntimeError("Git state changed during final evaluation")
    _write_json(out, report, private=split == "test")
    if ledger is not None:
        _write_json(ledger, {**reservation, "status": "completed", "completed_at_utc": _utc_now(),
                             "report_sha256": sha256_file(out)}, private=True)
    return report


def run_specs(paths: Iterable[str | Path], out: str | Path) -> dict[str, Any]:
    """Compatibility runner for the original example/spec-directory command."""
    paths = sorted(Path(path).resolve() for path in paths)
    for path in paths:
        reject_managed_dataset_input(path)
    if Path(out).resolve() in paths:
        raise ValueError("report output cannot overwrite an input specification")
    tasks = []
    for path in paths:
        spec = SceneSpec.load(path)
        tasks.append(SimpleNamespace(spec=spec, family="legacy_examples",
                                     task_id=spec.scene_id, spec_sha256=sha256_file(path)))
    report = evaluate_tasks(tasks, split="legacy")
    _write_json(Path(out), report)
    return report
