"""Read-only capability evidence; this module never authorizes execution.

The optional namespace check executes only a trusted system ``/bin/true`` through
the system unshare executable. It is not a candidate sandbox test. Inventory and
namespace creation alone cannot attest resource, credential or network isolation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
from typing import Any


SCHEMA_VERSION = "scene-rsi-capabilities-v1"
TOOLS = ("podman", "docker", "bwrap", "runc", "crun", "nerdctl", "nsjail",
         "runsc", "unshare", "prlimit", "systemd-run", "newuidmap", "newgidmap")
RUNTIMES = ("podman", "docker", "bwrap", "runc", "crun", "nerdctl", "nsjail", "runsc")
SYSCTLS = ("/proc/sys/kernel/unprivileged_userns_clone",
           "/proc/sys/user/max_user_namespaces",
           "/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
PROBE_TIMEOUT_SECONDS = 5
PROBE_OUTPUT_BYTES = 4096


def _read(path: str | Path, limit: int = 131072) -> tuple[str | None, str | None]:
    """Read bounded, explicitly allowlisted public system metadata only."""
    try:
        with Path(path).open("rb") as stream:
            value = stream.read(limit + 1)
        if len(value) > limit:
            return None, "metadata_exceeds_read_limit"
        return value.decode("utf-8", errors="replace"), None
    except OSError as exc:
        return None, type(exc).__name__


def _subordinate_entries(path: str, username: str | None, uid: int) -> dict[str, Any]:
    content, error = _read(path)
    if content is None:
        return {"readable": False, "error": error, "current_user_has_entries": None,
                "current_user_entry_count": None}
    names = {str(uid)}
    if username is not None:
        names.add(username)
    # No other user's identity/range, nor even their entry count, is exported.
    count = sum(1 for line in content.splitlines()
                if len(line.split(":")) == 3 and line.split(":", 1)[0] in names)
    return {"readable": True, "error": None, "current_user_has_entries": count > 0,
            "current_user_entry_count": count}


def _mount_path(value: str) -> str:
    for before, after in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(before, after)
    return value


def _cgroups() -> dict[str, Any]:
    content, error = _read("/proc/self/mountinfo")
    mounts: list[dict[str, Any]] = []
    for line in (content or "").splitlines():
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        before, after = left.split(), right.split()
        if len(before) < 6 or not after or after[0] not in ("cgroup", "cgroup2"):
            continue
        mount = _mount_path(before[4])
        options = before[5].split(",")
        entry: dict[str, Any] = {
            "mount_point": mount, "filesystem": after[0],
            "read_only": "ro" in options, "writable_by_current_process": os.access(mount, os.W_OK),
        }
        if after[0] == "cgroup2":
            for name in ("cgroup.controllers", "cgroup.subtree_control"):
                text, read_error = _read(Path(mount) / name, 4096)
                entry[name] = {"values": text.split() if text is not None else None,
                               "error": read_error}
        mounts.append(entry)
    return {"mounts": mounts, "read_error": error,
            "writable_mount_observed": any(item["writable_by_current_process"] and not item["read_only"]
                                           for item in mounts),
            "delegation_verified": False}


def _process_security() -> dict[str, Any]:
    content, error = _read("/proc/self/status")
    allowed = {"NoNewPrivs", "Seccomp", "Seccomp_filters", "CapEff", "CapPrm", "CapBnd"}
    fields = {}
    for line in (content or "").splitlines():
        name, separator, value = line.partition(":")
        if separator and name in allowed:
            fields[name] = value.strip()
    return {"fields": fields, "read_error": error}


def _trusted_binary(path: str, allowed: tuple[str, ...]) -> bool:
    """Only execute root-owned system binaries with non-writable ancestry."""
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            return False
        resolved = candidate.resolve(strict=True)
        if resolved not in {Path(item).resolve(strict=True) for item in allowed}:
            return False
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
            return False
        for part in (resolved, *resolved.parents):
            info = part.stat()
            if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return False
        return True
    except OSError:
        return False


def _output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    data = value.encode("utf-8", errors="replace") if isinstance(value, str) else value
    return data[:PROBE_OUTPUT_BYTES].decode("utf-8", errors="replace")


def _namespace_probe(unshare: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"requested": True, "status": "not_run", "success": False,
                              "timeout_seconds": PROBE_TIMEOUT_SECONDS,
                              "output_limit_bytes_per_stream": PROBE_OUTPUT_BYTES}
    if not unshare or not _trusted_binary(unshare, ("/usr/bin/unshare", "/bin/unshare")):
        result["reason"] = "trusted_system_unshare_unavailable"
        return result
    if not _trusted_binary("/bin/true", ("/bin/true", "/usr/bin/true")):
        result["reason"] = "trusted_system_true_unavailable"
        return result
    command = [str(Path(unshare).resolve()), "--user", "--map-root-user", "--mount",
               "--net", "--pid", "--fork", "/bin/true"]
    result["argv"] = command
    try:
        # Both executables are trusted system binaries, and the payload is a
        # literal no-op. Never extend this helper to accept a caller's command.
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=PROBE_TIMEOUT_SECONDS,
                                   check=False, close_fds=True, cwd="/",
                                   env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        result.update(status="completed", returncode=completed.returncode,
                      success=completed.returncode == 0, stdout=_output(completed.stdout),
                      stderr=_output(completed.stderr),
                      output_truncated=(len(completed.stdout) > PROBE_OUTPUT_BYTES or
                                        len(completed.stderr) > PROBE_OUTPUT_BYTES))
    except subprocess.TimeoutExpired as exc:
        result.update(status="timeout", reason="trusted_namespace_probe_timed_out",
                      stdout=_output(exc.stdout), stderr=_output(exc.stderr))
    except OSError as exc:
        result.update(status="error", reason=type(exc).__name__)
    return result


def probe_capabilities(namespace_probe: bool = False) -> dict[str, Any]:
    """Return public metadata for the current boundary, with live use disabled.

    ``namespace_probe=False`` performs no subprocess calls. An explicit true
    enables the bounded system no-op probe, never generated code or a container.
    Every returned capability is an observation, not authorization or attestation.
    """
    if type(namespace_probe) is not bool:
        raise ValueError("namespace_probe must be a boolean")
    uid = os.getuid()
    try:
        username = pwd.getpwuid(uid).pw_name
    except KeyError:
        username = None
    tool_paths = {name: shutil.which(name) for name in TOOLS}
    sysctls = {}
    for path in SYSCTLS:
        value, error = _read(path, 4096)
        sysctls[path] = {"value": value.strip() if value is not None else None, "error": error}
    cgroups = _cgroups()
    probe = (_namespace_probe(tool_paths["unshare"]) if namespace_probe else
             {"requested": False, "status": "not_requested", "success": None})
    blockers = [
        {"code": "backend_not_implemented",
         "detail": "No independently enforced proposal or candidate isolation backend is implemented."},
        {"code": "credential_broker_not_implemented",
         "detail": "No model endpoint broker protects host credentials from proposal tools."},
        {"code": "controller_safety_tests_not_passed",
         "detail": "No implemented backend has passed live filesystem/network/resource isolation regressions."},
    ]
    if not any(tool_paths[name] for name in RUNTIMES):
        blockers.append({"code": "runtime_not_found_on_path",
                         "detail": "No allowlisted container/sandbox runtime was found on the current PATH."})
    if cgroups["mounts"] and not cgroups["writable_mount_observed"]:
        blockers.append({"code": "no_writable_cgroup_mount_observed",
                         "detail": "No writable cgroup mount was observed in the current execution boundary."})
    if namespace_probe and not probe["success"]:
        blockers.append({"code": "namespace_probe_not_successful",
                         "detail": "The explicit trusted no-op namespace probe did not establish namespace creation."})
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "current_controller_execution_boundary_only",
        "offline_evidence_only": not namespace_probe,
        "backend_implemented": False,
        "credential_broker_implemented": False,
        "controller_safety_tests_passed": False,
        "live_allowed": False,
        "candidate_execution_allowed": False,
        "proposal_invocation_allowed": False,
        "observations": {
            "identity": {"uid": uid, "gid": os.getgid()}, "tools": tool_paths,
            "sysctls": sysctls,
            "subordinate_mappings": {name: _subordinate_entries("/etc/" + name, username, uid)
                                     for name in ("subuid", "subgid")},
            "cgroups": cgroups, "process_security": _process_security(),
            "namespace_probe": probe,
        },
        "blockers": blockers,
        "notes": [
            "A binary or successful namespace creation is not isolation attestation.",
            "Missing PATH entries or read-only mounts do not establish global host incapability.",
            "No user bus, service, container, GPU, network endpoint, home or credential file was accessed.",
            "Offline mock tests do not verify operating-system isolation.",
        ],
    }
