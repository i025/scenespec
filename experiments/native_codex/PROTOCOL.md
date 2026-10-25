# Native coding backend: capability trial, then K=1

Base 9377e67. Freeze this protocol and implementation before real inference.
No M2, extra agents, new scenes/assets, scorer changes or candidate-selection changes.
M0/M1 files stay byte-for-byte unchanged. Use the original request_for mapping
(common dev plus selected fixed-cost probe) and exact continuous eligible rule.

## Public interface and delivery

The common adapter produces an improvement task: objective, unchanged strategy
analysis/instructions, dev feedback, algorithm.py initial contents, allowed_files,
continuous API contract and budget. A coding backend receives this task and returns
status, Git patch, summary and available costs. Core policy/selection code never
parses Codex events. A no-CLI mock API backend must pass the same patch-consumer test.

Native Codex reads TASK.json, CONTRACT.md and algorithm.py in an independent Git
workspace. No output schema or complete-source JSON is requested. Only algorithm.py
may change. The host collects a Git diff and candidate Git version. Root project Git
history, hidden packets, witnesses, task builder, evaluator, assets and controller
logs are not copied/mounted. Model-side Git contains only the initial algorithm and
public development materials, not a sparse checkout of the full project.

Historical strategy text retains its original presentation wording. The common
adapter changes only delivery to in-place editing; research objectives, failure
analysis, probe choice and example selection are not rewritten.

## Editing environment (not a general Python execution sandbox)

Use existing Apptainer user/network/PID/mount isolation, no home/credentials/GPU,
no external network. Only its bounded model Unix broker is mounted. Workspace and
CLI state use the existing 64MiB session tmpfs; task, contract and Git are read-only.
Only bounded final source is exported to a single host file. Editor commands are
pwd, ls, rg --files and cat of the three task files. Native apply_patch edits the
algorithm. The trusted C reader is not a shell interpreter. Kernel Landlock denies
execution of interpreters and arbitrary programs; allowed executable inodes are
Codex, its code-mode host and the fixed reader. Native CLI's inner sandbox is disabled
ONLY inside this externally restricted container, because its nested file sandbox
prevented patch writes. There is no ordinary host execution fallback.

CPU affinity is one core and cannot expand after launch. Each native process has
45 CPU seconds, 16GiB virtual AS (V8's address reservation, not an RSS measurement),
8MiB file limit, 128 descriptors and zero core dumps. RLIMIT_NPROC=4096 counts the
SHARED host UID and is NOT a 32-process container limit. Therefore this editor never
opens arbitrary program execution; all generated layout code is executed exclusively
by the unchanged LayoutProcess (256MiB, 5 CPU seconds, no fork/open/socket).
Systemd user scope properties were tested but actual controllers did not enforce
them; they are not relied upon. Do not describe editor limits as a generic sandbox
for arbitrary Python or as an aggregate 16GiB physical-memory cgroup limit.

Before real inference require actual native read/edit/diff on a local fake-model
server, private-path and interpreter rejection, protected input/Git mounts, no GPU
and <=64MiB scratch. Existing eight malicious-candidate regressions must also pass.
No model is called during this offline acceptance.

## Budgets and steps

Same model gpt-6.1-sol, medium, CLI 0.163.0 and tools for all conditions.
Per coding task: 600s CLI wall, <=6 upstream requests (tool iterations, not six
independent candidates), <=196608 bytes/request and <=1048576 cumulative request
bytes, <=4096 output tokens/request. Save actual service usage and JSONL; unknown
usage stays unknown. No automatic fresh coding-task retry. This differs from the
old 180s/one-request JSON experiment: completion differences cannot be attributed
solely to the editing format, independent of budget/tools.

1. ONE unscored real editing task on original repair, two PUBLIC dev conditions,
   seeds 123/456. It asks for a small general implementation change, no root-written
   algorithm repair hints. Gate: completed nonempty patch, original static audit,
   changed function implementation (not just METHOD/docstrings), no runtime errors,
   24 queries in each run and 4/4 hard-valid outputs. Otherwise STOP before formal.
   Its patch/feedback is not supplied to any formal proposal.
2. If passed, FOUR formal K=1 coding tasks: M0/M1 x repair/joint, at most two
   concurrent editors. Reserve all four before inference. Use initial code, never
   the smoke patch. Existing eight dev packets, two fixed algorithm seeds each;
   fresh private 8 selection/12 test conditions from the unchanged distribution,
   two shared seeds each. Verify all 20 private references before formal inference.
3. Models receive dev only. Evaluate candidates with unchanged MeshWorld through
   original restricted LayoutProcess, serially for CPU accounting. Maximum 24 queries;
   external independent final gravity/navigation. Original eligible rule determines
   selection; a failed proposal leaves initial source selected. No manual patching.
4. Close all proposals before test. Evaluate original and all complete audited raw
   candidates on the new private test, report raw and selected performance. No
   reselection/tuning after test. No K2 or extra independent repeat in this round.

Report completion at separate levels: CLI completed, nonempty patch, static pass,
valid dynamic output, selected multiobjective improvement, heldout improvement.
Hard success, preference error, visibility, seed-pair position/rotation diversity,
valid-pair counts, CPU/wall and actual Token costs. Task-cluster paired intervals
are not model-sampling uncertainty. Four formal coding tasks cannot prove a general
meta-policy advantage or stable long-run completion; do not claim RSI.
