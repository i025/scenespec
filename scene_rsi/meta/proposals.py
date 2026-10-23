"""Offline P2A proposal preparation and audit. No model or candidate is launched.

Filesystem snapshots and a trusted preparation registry protect request input;
they are not a sandbox. The only live-request endpoint permanently fails closed.
This module deliberately has no injectable execution adapter or approval flag.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import time
from typing import Any
import uuid

from . import fixed

PROPOSAL_PROTOCOL = "fixed-meta-proposal-offline-v1"
MAX_FILE_BYTES = 1048576
MAX_BUNDLE_BYTES = 32 * MAX_FILE_BYTES
MAX_BUNDLE_FILES = 4096
MODEL_ROUTE = "/v1/responses"


@dataclass(frozen=True)
class ProposalLimits:
    """Required future external limits; declaring them does not enforce them."""

    wall_seconds: int = 120
    cpu_seconds: int = 60
    memory_bytes: int = 536870912
    processes: int = 64
    stdout_bytes: int = 1048576
    stderr_bytes: int = 65536
    patch_bytes: int = 262144
    input_tokens: int = 32768
    output_tokens: int = 8192
    total_reserved_tokens: int = 327680
    total_reserved_wall_seconds: int = 960

    def validate(self) -> None:
        ceilings = {
            "wall_seconds": 3600, "cpu_seconds": 3600,
            "memory_bytes": 8 * 1024 ** 3, "processes": 1024,
            "stdout_bytes": MAX_FILE_BYTES, "stderr_bytes": MAX_FILE_BYTES,
            "patch_bytes": fixed.MAX_INGEST_BYTES, "input_tokens": 1000000,
            "output_tokens": 65536, "total_reserved_tokens": 8000000,
            "total_reserved_wall_seconds": 86400,
        }
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError(f"{name} must be an integer in [1, {ceiling}]")
        if self.input_tokens + self.output_tokens > self.total_reserved_tokens:
            raise ValueError("total_reserved_tokens cannot fund one declared proposal")
        if self.wall_seconds > self.total_reserved_wall_seconds:
            raise ValueError("total_reserved_wall_seconds cannot fund one declared proposal")


class _InputTooLarge(ValueError):
    def __init__(self, prefix: bytes, size: int):
        super().__init__("input exceeds the bounded regular-file read limit")
        self.prefix, self.size = prefix, size


def _strict_json(data: bytes) -> Any:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"nonfinite JSON constant: {value}")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("nonfinite JSON number")
        return result

    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=invalid_constant, parse_float=finite_float)


@contextmanager
def _directory_fd(path: Path):
    """Open every directory component without following a symlink."""
    absolute = Path(os.path.abspath(path))
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        yield descriptor
    finally:
        os.close(descriptor)


def _read_descriptor(descriptor: int, maximum: int) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("input must be a regular file, never a FIFO/device/socket")
    chunks, remaining = [], maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(remaining, 65536))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > maximum or before.st_size > maximum:
        raise _InputTooLarge(data[:maximum], before.st_size)
    after = os.fstat(descriptor)
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
        raise ValueError("input changed during its bounded snapshot read")
    return data


def _read_regular(path: Path, maximum: int) -> bytes:
    path = Path(os.path.abspath(path))
    with _directory_fd(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                             dir_fd=parent)
        try:
            return _read_descriptor(descriptor, maximum)
        finally:
            os.close(descriptor)


def _read_bundle(bundle: Path) -> dict[str, bytes]:
    payloads, directories, total = {}, set(), 0

    def walk(descriptor, prefix=""):
        nonlocal total
        if prefix.count("/") > 32:
            raise ValueError("proposal bundle nesting exceeds its depth limit")
        with os.scandir(descriptor) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            relative = prefix + name
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                directories.add(relative)
                if len(directories) + len(payloads) > MAX_BUNDLE_FILES:
                    raise ValueError("proposal bundle contains too many entries")
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                try:
                    walk(child, relative + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                try:
                    data = _read_descriptor(child, MAX_FILE_BYTES)
                finally:
                    os.close(child)
                payloads[relative] = data
                total += len(data)
                if total > MAX_BUNDLE_BYTES or len(payloads) + len(directories) > MAX_BUNDLE_FILES:
                    raise ValueError("proposal bundle exceeds its total byte/entry limit")
            else:
                raise ValueError("proposal bundle cannot contain symlinks or special files")

    with _directory_fd(bundle) as root:
        walk(root)
    expected_dirs = {parent.as_posix() for name in payloads for parent in Path(name).parents
                     if parent.as_posix() != "."}
    if directories != expected_dirs:
        raise ValueError("proposal bundle has unexpected empty directories")
    return payloads


def _write_snapshot(destination: Path, payloads: dict[str, bytes]) -> None:
    destination.mkdir(mode=0o700, exist_ok=False)
    for name, data in sorted(payloads.items()):
        fixed._write(destination / name, data)
        (destination / name).chmod(0o400)
    for directory in sorted((p for p in destination.rglob("*") if p.is_dir()), reverse=True):
        directory.chmod(0o500)
    destination.chmod(0o500)


def _checked_snapshot(bundle: Path) -> tuple[dict, dict[str, bytes]]:
    payloads = _read_bundle(Path(bundle))
    if "request.json" not in payloads:
        raise ValueError("proposal bundle lacks request.json")
    parsed = _strict_json(payloads["request.json"])
    with TemporaryDirectory(prefix="scene-rsi-request-") as temporary:
        snapshot = Path(temporary) / "bundle"
        _write_snapshot(snapshot, payloads)
        request = fixed._validate_bundle(snapshot)
    if parsed != request:
        raise ValueError("inconsistent request snapshot")
    return request, payloads


def _controls(repo: Path) -> Path:
    common = Path(fixed._git(repo, "rev-parse", "--git-common-dir").decode().strip())
    common = (repo / common).resolve() if not common.is_absolute() else common.resolve()
    return common / "scene-rsi-meta"


def register_prepared_request(repo: Path, bundle: Path, request: dict) -> None:
    """Called by trusted prepare_request; registry is never exported to a model.

    This is an operator-controlled registry, not a signature or protection against
    another process running as the same user and editing Git administrative data.
    """
    repo = Path(repo).resolve()
    checked, payloads = _checked_snapshot(bundle)
    if request != checked or fixed._parent(repo) != checked["parent_git"]:
        raise ValueError("prepared request does not match the clean committed parent")
    prepared = _controls(repo) / "prepared"
    prepared.mkdir(parents=True, mode=0o700, exist_ok=True)
    registration = {"registry_version": 1, "request_id": request["request_id"],
                    "request_sha256": fixed._sha(payloads["request.json"])}
    target = prepared / (request["request_id"] + ".json")
    with (prepared / ".registration.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists():
            if _strict_json(_read_regular(target, MAX_FILE_BYTES)) != registration:
                raise ValueError("trusted prepared-request registration mismatch")
        else:
            fixed._write(target, fixed._json_bytes(registration))


def _authenticated(repo: Path, bundle: Path) -> tuple[dict, dict[str, bytes]]:
    request, payloads = _checked_snapshot(bundle)
    target = _controls(repo) / "prepared" / (request["request_id"] + ".json")
    try:
        registration = _strict_json(_read_regular(target, MAX_FILE_BYTES))
    except FileNotFoundError as error:
        raise ValueError("bundle was not registered by trusted prepare_request") from error
    expected = {"registry_version": 1, "request_id": request["request_id"],
                "request_sha256": fixed._sha(payloads["request.json"])}
    if registration != expected:
        raise ValueError("trusted prepared-request registration mismatch")
    parent = fixed._parent(repo)
    if parent != request["parent_git"]:
        raise ValueError("request parent does not match the clean current Git parent")
    for name, data in fixed._source_blobs(repo, parent["commit"]).items():
        if payloads.get("source/" + name) != data:
            raise ValueError("request source differs from its committed parent")
    return request, payloads


def _plan(request: dict, limits: ProposalLimits | None) -> dict:
    limits = limits or ProposalLimits()
    limits.validate()
    model = request["config"]["model"]
    if not model:
        raise ValueError("a pinned model identifier is required even for an offline proposal plan")
    if limits.patch_bytes > request["config"]["max_patch_bytes"]:
        raise ValueError("proposal patch limit exceeds the fixed request patch budget")
    declared = asdict(limits)
    return {
        "proposal_protocol": PROPOSAL_PROTOCOL, "request_id": request["request_id"],
        "request_protocol": request["protocol_version"], "parent_git": request["parent_git"],
        "model": model, "prompt_sha256": request["prompt_sha256"],
        "config_sha256": request["config_sha256"], "execute": False,
        "status": "blocked", "reason": "missing_verified_backend",
        "argv": ["codex", "exec", "--sandbox", "read-only", "--ignore-user-config",
                 "--ephemeral", "--skip-git-repo-check", "--json", "--output-schema",
                 "/workspace/proposal.schema.json", "--output-last-message",
                 "/output/proposal.json", "--model", model, "-"],
        "cwd": "/workspace", "stdin": fixed.PROMPT,
        "expected_cli_version": "0.163.0 (plan only; no runtime version attestation)",
        "limits": declared, "limits_sha256": fixed._sha(fixed._json_bytes(declared)),
        "limits_enforced": False,
        "required_boundary": {
            "read_only_mounts": ["authenticated_request_snapshot:/workspace"],
            "writable_mounts": ["empty_output:/output", "empty_bounded_tmp:/tmp"],
            "forbidden_mounts": ["controller_repository", "dataset", "reference_solutions",
                                 "controller_logs", "user_home", "credentials", "GPU_devices"],
            "privileges": "non-root, no capabilities, no host namespaces",
            "network": "only credential-free model broker; all other egress denied",
            "model_route": MODEL_ROUTE,
            "authentication": "credential injection outside model/tool filesystem and environment",
            "environment": "empty by default; no inherited HOME, CODEX_HOME, auth, SSH or proxy values",
            "resource_enforcement": "external CPU/memory/PID/wall/output/disk limits required",
            "token_enforcement": "broker hard request/input/output/total budgets required",
        },
        "is_rsi": False,
    }


def safe_plan(repo: Path, bundle: Path, limits: ProposalLimits | None = None) -> dict:
    """Authenticate and snapshot a dev-only request; return a non-executable plan."""
    request, _ = _authenticated(Path(repo).resolve(), Path(bundle))
    return _plan(request, limits)


def reserve_proposal_attempt(repo: Path, bundle: Path, out: Path, *,
                             limits: ProposalLimits | None = None,
                             source: str = "blocked_request") -> dict:
    """Persist one shared P1/P2 quota slot before a proposal could be launched."""
    start, before = time.monotonic(), fixed._usage()
    if source not in {"blocked_request", "offline_fixture"}:
        raise ValueError("only blocked_request and offline_fixture sources are supported")
    repo, bundle, out = Path(repo).resolve(), Path(os.path.abspath(bundle)), Path(os.path.abspath(out))
    request, payloads = _authenticated(repo, bundle)
    plan = _plan(request, limits)
    if out == repo or repo in out.parents or out == bundle or bundle in out.parents:
        raise ValueError("proposal audit output must be outside the repository and request bundle")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _directory_fd(out):
        pass
    started = datetime.now(timezone.utc).isoformat()
    with (out / ".audit.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fixed._bind_audit_root(repo, request["request_id"], out)
        prior = []
        for path in out.glob("candidate-*/reservation.json"):
            reservation = _strict_json(_read_regular(path, MAX_FILE_BYTES))
            if reservation.get("request_id") == request["request_id"]:
                prior.append(reservation)
        # Bind limits with the first proposal; a later caller cannot raise them.
        binding = _controls(repo) / (request["request_id"] + ".proposal-limits.json")
        configured = {"request_id": request["request_id"], "limits": plan["limits"]}
        if binding.exists():
            if _strict_json(_read_regular(binding, MAX_FILE_BYTES)) != configured:
                raise ValueError("proposal limits are already fixed for this request")
        else:
            fixed._write(binding, fixed._json_bytes(configured))
        index = len(prior) + 1
        candidate_id = "candidate-" + uuid.uuid4().hex
        directory = out / candidate_id
        directory.mkdir(mode=0o700)
        tokens = plan["limits"]["input_tokens"] + plan["limits"]["output_tokens"]
        reserved_tokens = sum(row.get("reserved_tokens", 0) for row in prior) + tokens
        reserved_wall = sum(row.get("reserved_wall_seconds", 0) for row in prior) + plan["limits"]["wall_seconds"]
        reason = None
        if index > request["config"]["max_candidates"]:
            reason = "candidate_budget_exhausted"
        elif reserved_tokens > plan["limits"]["total_reserved_tokens"]:
            reason = "declared_token_budget_exhausted"
        elif reserved_wall > plan["limits"]["total_reserved_wall_seconds"]:
            reason = "declared_wall_budget_exhausted"
        reservation = {
            "request_id": request["request_id"], "candidate_id": candidate_id,
            "attempt_index": index, "started_at_utc": started, "source": source,
            "status": "budget_rejected" if reason else "reserved", "reason": reason,
            "parent_git": request["parent_git"], "model": plan["model"],
            "proposal_protocol": PROPOSAL_PROTOCOL, "limits_sha256": plan["limits_sha256"],
            "reserved_tokens": tokens, "reserved_wall_seconds": plan["limits"]["wall_seconds"],
        }
        fixed._write(directory / "reservation.json", fixed._json_bytes(reservation))
        result = {**reservation, "reservation_path": str(directory / "reservation.json"),
                  "directory": str(directory), "plan": plan}
        try:
            fixed._write(directory / "plan.json", fixed._json_bytes(plan))
            _write_snapshot(directory / "request_snapshot", payloads)
        except (ValueError, OSError) as error:
            result.update(status="preparation_failed", reason="request_snapshot_failed")
            try:
                result["final_record"] = _finish(result, "rejected", "request_snapshot_failed",
                                                 start=start, before=before, error=error)
            except OSError as final_error:
                raise ValueError(f"consumed reservation {result['reservation_path']}; "
                                 "storage also prevented its failure record") from final_error
    return result


def _finish(reservation: dict, status: str, reason: str, *, start: float,
            before: dict | None = None,
            candidate_record: dict | None = None, fixture_usage: dict | None = None,
            error: Exception | None = None, archives: dict | None = None) -> dict:
    directory = Path(reservation["directory"])
    after = fixed._usage()
    record = {
        "record_version": 1, "proposal_protocol": PROPOSAL_PROTOCOL,
        "candidate_id": reservation["candidate_id"], "request_id": reservation["request_id"],
        "attempt_index": reservation["attempt_index"], "source": reservation["source"],
        "started_at_utc": reservation["started_at_utc"],
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status, "reason": reason, "model": reservation["model"],
        "parent_git": reservation["parent_git"], "limits_sha256": reservation["limits_sha256"],
        "prompt_sha256": reservation["plan"]["prompt_sha256"],
        "config_sha256": reservation["plan"]["config_sha256"],
        "request_protocol": reservation["plan"]["request_protocol"],
        "cli_version_observed": None, "model_invocation_status": "not_run",
        "execution": {"status": "not_run", "reason": "missing_verified_backend"},
        "test_results": {"unit_tests": "not_run", "dev": "not_run", "val": "not_run", "test": "not_run"},
        "resources": {"model_calls": 0, "model_tokens": None, "model_cost": None,
                      "controller_wall_seconds": time.monotonic() - start,
                      "controller_cpu_seconds": (max(0, after["cpu_seconds"] - before["cpu_seconds"])
                                                 if before is not None else None),
                      "controller_peak_rss_bytes": after["controller_peak_rss_bytes"],
                      "children_peak_rss_bytes": after["children_peak_rss_bytes"],
                      "rss_scope": "process lifetime high-water marks, not per-attempt peak attribution",
                      "cpu_scope": "controller plus trusted Git children; no model/candidate execution",
                      "gpu_used": False},
        "fixture_usage": fixture_usage, "candidate_record": candidate_record,
        "archives": archives or {}, "is_rsi": False,
    }
    if error is not None:
        record["error"] = {"type": type(error).__name__, "message": str(error)[:2000]}
    path = directory / "proposal_record.json"
    fixed._write(path, fixed._json_bytes(record))
    return {**record, "proposal_record_path": str(path),
            "reservation_path": reservation["reservation_path"]}


def request_proposal(repo: Path, bundle: Path, out: Path, *,
                     limits: ProposalLimits | None = None) -> dict:
    """Record a denied live request. No Codex subprocess/network path exists."""
    start, before = time.monotonic(), fixed._usage()
    reservation = reserve_proposal_attempt(repo, bundle, out, limits=limits)
    if "final_record" in reservation:
        return reservation["final_record"]
    if reservation["status"] == "budget_rejected":
        return _finish(reservation, "rejected", reservation["reason"], start=start, before=before)
    return _finish(reservation, "blocked", "missing_verified_backend", start=start, before=before)


def parse_proposal(data: bytes, max_patch_bytes: int = 262144) -> dict:
    if type(data) is not bytes or len(data) > MAX_FILE_BYTES:
        raise ValueError("proposal output must be bounded UTF-8 JSON bytes")
    if type(max_patch_bytes) is not int or not 0 < max_patch_bytes <= fixed.MAX_INGEST_BYTES:
        raise ValueError("max_patch_bytes must be a positive integer within the hard ingest cap")
    result = _strict_json(data)
    if type(result) is not dict or set(result) != {"patch", "rationale"}:
        raise ValueError("proposal must contain exactly patch and rationale")
    if any(type(result[key]) is not str or not result[key].strip() for key in result):
        raise ValueError("proposal patch and rationale must be nonempty strings")
    if "\x00" in result["patch"] or len(result["patch"].encode("utf-8")) > max_patch_bytes:
        raise ValueError("proposal patch is binary or exceeds max_patch_bytes")
    return result


def parse_codex_usage(data: bytes) -> dict | None:
    """Parse known CLI JSONL usage fields; unknown/missing counts stay unknown."""
    if type(data) is not bytes or len(data) > MAX_FILE_BYTES:
        raise ValueError("Codex event stream must be bounded JSONL bytes")
    usage, terminal = None, None
    for line in data.splitlines():
        if not line.strip():
            continue
        event = _strict_json(line)
        if type(event) is not dict or type(event.get("type")) is not str:
            raise ValueError("Codex JSONL events must be typed JSON objects")
        if event["type"] not in {"turn.completed", "turn.failed", "error"}:
            continue
        if terminal is not None:
            raise ValueError("multiple or contradictory terminal events in one proposal stream")
        terminal = event["type"]
        if terminal != "turn.completed":
            continue
        value = event.get("usage")
        if value is None:
            continue
        if type(value) is not dict:
            raise ValueError("Codex usage must be an object or null")
        known = {}
        for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
            count = value.get(key)
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("token counts must be nonnegative integers or null")
            known[key] = count
        if known["cached_input_tokens"] is not None and known["input_tokens"] is not None:
            if known["cached_input_tokens"] > known["input_tokens"]:
                raise ValueError("cached_input_tokens exceeds input_tokens")
        if any(count is not None for count in known.values()):
            known["total_tokens"] = (known["input_tokens"] + known["output_tokens"]
                                     if known["input_tokens"] is not None and known["output_tokens"] is not None
                                     else None)
            usage = known
    return usage


def ingest_offline_proposal(repo: Path, bundle: Path, proposal: Path, out: Path, *,
                            events: Path | None = None,
                            limits: ProposalLimits | None = None) -> dict:
    """Archive a clearly labelled fixture, parse it and run only P1 static audit."""
    start, before = time.monotonic(), fixed._usage()
    reservation = reserve_proposal_attempt(repo, bundle, out, limits=limits, source="offline_fixture")
    if "final_record" in reservation:
        return reservation["final_record"]
    if reservation["status"] == "budget_rejected":
        return _finish(reservation, "rejected", reservation["reason"], start=start, before=before)
    directory, usage, archives, raw = Path(reservation["directory"]), None, {}, {}
    try:
        for name, path in (("model-output.json", proposal), ("codex-events.jsonl", events)):
            if path is None:
                continue
            try:
                data = _read_regular(Path(path), reservation["plan"]["limits"]["stdout_bytes"])
            except _InputTooLarge as error:
                fixed._write(directory / (name + ".rejected-prefix"), error.prefix)
                archives[name] = {"complete": False, "prefix_bytes": len(error.prefix),
                                  "prefix_sha256": fixed._sha(error.prefix), "full_sha256": None}
                raise
            fixed._write(directory / name, data)
            archives[name] = {"complete": True, "bytes": len(data), "sha256": fixed._sha(data)}
            raw[name] = data
        parsed = parse_proposal(raw["model-output.json"], reservation["plan"]["limits"]["patch_bytes"])
        if "codex-events.jsonl" in raw:
            usage = parse_codex_usage(raw["codex-events.jsonl"])
        patch = directory / "offline-input.patch"
        fixed._write(patch, parsed["patch"].encode("utf-8"))
        fixed._write(directory / "rationale.txt", parsed["rationale"].encode("utf-8"))
        candidate = fixed.record_candidate(repo, directory / "request_snapshot", patch, out,
                                           reserved_candidate_id=reservation["candidate_id"])
        return _finish(reservation, candidate["status"], "offline_static_audit_only", start=start, before=before,
                       candidate_record=candidate, fixture_usage=usage, archives=archives)
    except (ValueError, OSError, KeyError, TypeError, UnicodeError, RecursionError) as error:
        return _finish(reservation, "rejected", "invalid_offline_fixture", start=start, before=before,
                       fixture_usage=usage, error=error, archives=archives)
