"""Candidate execution contract. No isolation adapter is installed or invoked.

The controller may read an audited Git version as data. It never imports that
version, checks it out onto sys.path, or substitutes an ordinary subprocess.
Offline captures are explicitly labelled and cannot enable CandidateRunner.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import re
import time

from ..schema import SceneSpec, SceneState
from . import fixed
from .proposals import _directory_fd, _read_regular, _strict_json


@dataclass(frozen=True)
class RunLimits:
    cpu_cores: int = 1
    cpu_seconds: int = 10
    memory_bytes: int = 268435456
    pids: int = 16
    wall_seconds: int = 15
    input_bytes: int = 1048576
    stdout_bytes: int = 1048576
    stderr_bytes: int = 65536
    temporary_bytes: int = 16777216

    def validate(self) -> None:
        ceilings = (8, 300, 2 * 1024**3, 128, 600, 1048576, 1048576, 1048576, 256 * 1024**2)
        for (name, value), ceiling in zip(asdict(self).items(), ceilings):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError(f"{name} must be an integer in [1, {ceiling}]")


@dataclass(frozen=True)
class AuditedCandidate:
    audit_directory: str
    candidate_id: str
    commit: str
    parent_commit: str
    git_directory: str


def load_audited_candidate(audit_directory: Path) -> AuditedCandidate:
    """Read a controller-owned static audit and its archived Git version.

    The directory must stay private to the trusted controller. A JSON record
    is not authorization against a process already able to edit that directory.
    Git object names are version identifiers; no new file digests are computed.
    """
    directory = Path(audit_directory).absolute()
    record = _strict_json(_read_regular(directory / "record.json", 1048576))
    stages = {"bundle_integrity", "audit_root_binding", "candidate_budget", "parent_version",
              "patch_scope", "git_apply", "syntax_and_import_policy", "candidate_version"}
    if (type(record) is not dict or record.get("status") != "accepted_static_only"
            or record.get("record_version") != 1):
        raise ValueError("candidate requires an accepted controller static audit")
    candidate_id = record.get("candidate_id")
    if (type(candidate_id) is not str or not re.fullmatch(r"candidate-[0-9a-f]{32}", candidate_id)
            or directory.name != candidate_id):
        raise ValueError("audit directory and candidate identity differ")
    checks = record.get("checks")
    if (type(checks) is not list or any(type(row) is not dict or row.get("passed") is not True for row in checks)
            or {row.get("name") for row in checks} != stages):
        raise ValueError("candidate static audit is incomplete")
    version, parent = record.get("candidate_git"), record.get("parent_git")
    if (type(version) is not dict or type(parent) is not dict
            or version.get("repository") != "candidate.git"):
        raise ValueError("candidate archive must be the controller's candidate.git")
    for commit in (version.get("commit"), parent.get("commit")):
        if type(commit) is not str or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            raise ValueError("candidate and parent must identify full Git commits")
    archive = directory / "candidate.git"
    with _directory_fd(archive):
        for ref, expected in (("candidate", version["commit"]), ("parent", parent["commit"])):
            observed = fixed._git(archive, "rev-parse", "--verify", f"refs/heads/{ref}^{{commit}}")
            if observed.decode().strip() != expected:
                raise ValueError("archived Git reference differs from static audit")
    return AuditedCandidate(str(directory), candidate_id, version["commit"], parent["commit"], str(archive))


def parse_spec(spec_json: bytes, limits: RunLimits) -> SceneSpec:
    limits.validate()
    if type(spec_json) is not bytes or len(spec_json) > limits.input_bytes:
        raise ValueError("SceneSpec input must be bounded JSON bytes")
    data = _strict_json(spec_json)
    fields = {"scene_id", "room", "required_objects", "assets", "regions", "constraints", "access_paths"}
    if type(data) is not dict or set(data) - fields:
        raise ValueError("input must contain only SceneSpec fields")
    return SceneSpec.from_dict(data)


def parse_state(stdout: bytes, limits: RunLimits) -> SceneState:
    limits.validate()
    if type(stdout) is not bytes or len(stdout) > limits.stdout_bytes:
        raise ValueError("SceneState output must be bounded JSON bytes")
    data = _strict_json(stdout)
    if (type(data) is not dict or set(data) != {"scene_id", "objects"}
            or type(data["scene_id"]) is not str or type(data["objects"]) is not list):
        raise ValueError("output must be one SceneState JSON object")
    for obj in data["objects"]:
        if (type(obj) is not dict or set(obj) != {"id", "asset", "x", "y", "yaw_deg"}
                or type(obj["id"]) is not str or type(obj["asset"]) is not str):
            raise ValueError("invalid placed object JSON")
        for name in ("x", "y", "yaw_deg"):
            value = obj[name]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("placed transforms must be finite JSON numbers")
    # Bounds, collisions, object completeness and allowed yaw remain Verifier decisions.
    return SceneState.from_dict(data)


def _result(mode: str, status: str, reason: str, limits: RunLimits) -> dict:
    return {
        "record_version": 1, "mode": mode, "status": status, "reason": reason,
        "execution_started": False, "limits_enforced": False,
        "candidate": None, "exit_code": None, "state": None,
        "limits": asdict(limits), "wall_seconds": None,
        "resources": {"cpu_seconds": None, "peak_memory_bytes": None, "peak_pids": None},
        "logs": {"stdout": "", "stderr": "", "stdout_bytes": 0, "stderr_bytes": 0,
                 "stdout_truncated": False, "stderr_truncated": False},
        "isolation_acceptance": "not_run", "gpu_accessed": False,
    }


class CandidateRunner:
    """Trusted, fail-closed interface; no callback, command or enable switch.

    A future accepted backend must enforce RunLimits on the entire process tree
    and pass the administrator acceptance matrix before this method can launch.
    """
    def __init__(self, limits: RunLimits | None = None):
        self.limits = limits or RunLimits()
        self.limits.validate()

    def run(self, candidate: AuditedCandidate, spec_json: bytes) -> dict:
        started = time.monotonic()
        if not isinstance(candidate, AuditedCandidate):
            raise ValueError("run requires an audited candidate Git version")
        checked = load_audited_candidate(Path(candidate.audit_directory))
        if checked != candidate:
            raise ValueError("candidate metadata differs from controller audit")
        parse_spec(spec_json, self.limits)
        result = _result("blocked", "blocked", "no_verified_isolation_backend", self.limits)
        result["candidate"] = asdict(checked)
        result["controller_wall_seconds"] = time.monotonic() - started
        return result


def offline_capture(stdout: bytes, *, stderr: bytes = b"", exit_code: int = 0,
                    termination: str = "exited", limits: RunLimits | None = None) -> dict:
    """Simulate a completed transport using inert controller-provided bytes.

    This does not enforce OS limits or execute anything. Runtime metrics remain
    unknown. Oversized fixture prefixes are bounded before decoding/archiving.
    """
    limits = limits or RunLimits()
    limits.validate()
    if type(stdout) is not bytes or type(stderr) is not bytes or type(exit_code) is not int:
        raise ValueError("fixture streams must be bytes and exit_code an integer")
    if termination not in {"exited", "timeout", "cpu_limit", "memory_limit", "pid_limit", "runtime_error"}:
        raise ValueError("unknown simulated termination")
    result = _result("offline_fixture", "captured", "synthetic_transport_only", limits)
    result["exit_code"] = exit_code if termination == "exited" else None
    for name, raw, cap in (("stdout", stdout, limits.stdout_bytes), ("stderr", stderr, limits.stderr_bytes)):
        result["logs"][name] = raw[:cap].decode("utf-8", errors="replace")
        result["logs"][name + "_bytes"] = min(len(raw), cap)
        result["logs"][name + "_truncated"] = len(raw) > cap
    if len(stdout) > limits.stdout_bytes or len(stderr) > limits.stderr_bytes:
        result.update(status="output_limit", reason="synthetic_output_exceeds_limit")
    elif termination != "exited":
        result.update(status=termination, reason="synthetic_termination")
    elif exit_code != 0:
        result.update(status="nonzero_exit", reason="synthetic_nonzero_exit")
    else:
        try:
            result["state"] = parse_state(stdout, limits).to_dict()
            result["status"] = "state_received"
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            result.update(status="invalid_state", reason="invalid_scene_state_json")
    return result
