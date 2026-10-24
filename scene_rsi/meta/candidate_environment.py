"""Read-only candidate-service inventory, never an execution authorization."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

from .capabilities import probe_capabilities


RUNTIMES = ("apptainer", "singularity", "podman", "docker", "bwrap", "runc",
            "crun", "nsjail", "runsc", "firejail")
SCHEDULERS = ("srun", "sbatch", "sinfo", "scontrol")
PREFIXES = ("/usr/bin", "/bin", "/usr/local/bin", "/opt/apptainer/bin",
            "/opt/singularity/bin", "/opt/slurm/bin", "/cm/shared/apps/slurm/current/bin")
CONFIGS = ("/etc/apptainer/apptainer.conf", "/etc/singularity/singularity.conf",
           "/etc/slurm/slurm.conf", "/etc/slurm-llnl/slurm.conf", "/run/podman/podman.sock")
MODULE_ROOTS = ("/etc/modulefiles", "/usr/share/modulefiles",
                "/usr/share/Modules/modulefiles", "/usr/share/lmod/lmod/modulefiles")
REQUIRED_BOUNDARIES = {
    "mounts": "Only read-only agent payload and one SceneSpec; no repository, dataset or host root.",
    "network": "No external, host-loopback, DNS, Unix service socket or metadata-service access.",
    "processes": "Private PID/IPC namespaces; no host process visibility or signalling; kill all descendants.",
    "resources": "Enforce aggregate CPU, memory/swap, PID, wall-time, output and temporary-disk limits.",
    "credentials": "No host Home, credential files, inherited secrets, forwarded sockets or open descriptors.",
    "devices": "No GPU devices or driver injection; minimal devices and no privilege escalation.",
}


def inspect_candidate_environment() -> dict:
    """Inspect public metadata only; do not start a runtime, job or probe payload.

    A tool's presence is an observation. Site approval and live boundary tests
    are separate requirements; neither can be supplied as a caller's boolean.
    """
    base = probe_capabilities()
    names = (*RUNTIMES, *SCHEDULERS)
    paths = {name: shutil.which(name) for name in names}
    fixed_paths = [str(Path(prefix) / name) for prefix in PREFIXES for name in names
                   if (Path(prefix) / name).is_file() and os.access(Path(prefix) / name, os.X_OK)]
    return {
        "report_version": 1, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "current_controller_boundary; not a global host or administrator attestation",
        "status": "blocked", "candidate_execution_allowed": False,
        "backend_implemented": False, "administrator_approval": "not_provided",
        "observations": {
            "tools_on_path": paths, "tools_at_fixed_prefixes": fixed_paths,
            "configuration_present": {name: Path(name).exists() for name in CONFIGS},
            "module_roots_present": {name: Path(name).exists() for name in MODULE_ROOTS},
            "slurm_environment_keys": sorted(key for key in os.environ if key.startswith("SLURM_")),
            "cgroups": base["observations"]["cgroups"],
            "process_security": base["observations"]["process_security"],
        },
        "boundaries": {name: {"status": "not_verified", "requirement": requirement,
                              "live_test_run": False}
                       for name, requirement in REQUIRED_BOUNDARIES.items()},
        "blockers": ["no_implemented_and_accepted_backend", "no_administrator_service_contract",
                     "no_live_boundary_acceptance", "no_validated_aggregate_resource_enforcement"],
        "candidate_processes_started": 0, "gpu_accessed": False,
        "notes": ["No credentials, Home contents, runtime socket, scheduler or network endpoint was accessed.",
                  "No namespace no-op, container, job or malicious fixture was executed.",
                  "An unknown private isolation service may exist; an administrator must provide its contract."],
    }


if __name__ == "__main__":
    print(json.dumps(inspect_candidate_environment(), indent=2))
