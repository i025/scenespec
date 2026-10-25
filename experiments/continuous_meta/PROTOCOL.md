# Continuous Mesh: frozen M0/M1 pilot protocol

Base commit 64ab246. The Continuous Mesh tasks, meshes, geometric evaluator,
physics/navigation, original baselines, rendering and historical scores stay
unchanged. No M2, meta-policy edits, new task family or model training.
Freeze this protocol, adapter and controller before any actual proposal.

## Boundary and matched adaptation

M0: scene_rsi/agent/meta_policy.py, unmodified.
M1: experiments/generalization_meta/results/meta/frozen_policy.py, unmodified.
Both run in the existing LayoutProcess Apptainer/seccomp runner, as do every
candidate. Credentials stay in the host proposal relay; a model sees only its
read-only request workspace and schema. It cannot read the repository, task
builder/reference code, hidden data or controller logs. No shell/tools offered.

The shared candidate API is layout(packet). For 24 query steps it returns
support-relative continuous triples for every movable object plus JSON memory.
The immutable original baseline.state converts these to actual six-DoF poses.
A candidate cannot change size, camera, architecture or membership. An external
MeshWorld performs one frozen geometry/pixel evaluation per step. A final
policy call chooses one of the 24 already queried outputs, with no extra query
or unqueried repair. All history is local to that ONE scene. Final gravity and
passage checks run in a fresh external MeshWorld after the candidate exits.
The model can change search, updates and final selection, not the transform or
evaluator. Positions are continuous on the original support-contact manifold;
this is not arbitrary free-body six-axis search.

The original repair/joint algorithms are transcribed into this state-machine
interface, preserving RNG streams, strict loss comparisons and candidate order.
First require exact output equality at 24 queries against the original trusted
algorithms on four dev families x two seeds x two methods (16 runs). No generated
Python is imported on host. The original eight live isolation regressions must
pass before candidate execution. random is preloaded by the original entry;
continuous static audit permits that module in addition to the old pure imports.
No kernel restriction or historical audit default is relaxed.

Each candidate scene: unchanged 256MiB address space, 5 CPU seconds inside the
single-process sandbox, process creation/file opens/network syscalls denied,
7s per response and 30s lifetime, <16KiB response and <=1MiB total stdout.
Outside: exactly 24 maximum Mesh/pixel queries, total query CPU threshold 5s.
Original baseline's CPU included query work in one process; the adapter splits
that work from the 5s candidate process ceiling. All compared initial/candidate
algorithms use this SAME adapter. Report overhead rather than equating transport
wall time to original in-process timing. Runtime/protocol failure is hard failure
and receives 10m preference penalty / zero visibility; no silent fallback.

## Tasks and privacy

Reuse the eight published dev task packets with two freshly drawn algorithm
seeds, fixed across all arms. Draw eight NEW private selection tasks and twelve
NEW final test tasks from the UNCHANGED four-family distribution, balanced 2/3
per family, with two fixed algorithm seeds each. SystemRandom draws actual seeds.
Check every new private constructive reference before evaluation; no candidate
gets references. Keep packets, seeds, witnesses and per-item heldout outputs
outside Git in a mode-700 run directory. Previous reported test is not reused.
Only dev packets, candidate sources/diffs/model calls and heldout aggregates can
be published. Test is not evaluated until ALL possible proposal calls have ended.
No selection/test data, per-item feedback or choice result enters a model request.

Both policies see eight common dev runs (dev0..3, two seeds each), then choose
one four-run probe through the same immutable mapping:
random = dev4..7, first seed;
barriers = dev5 and dev7, both seeds (doorway and paired separation);
dense = dev5 and dev6, both seeds (the largest two movable/fixed-object counts).
Policy data uses valid for preliminary geometry and solvable for ALL final hard
checks; primary quality is also included explicitly. Policy source and its old
domain wording are not edited. The outer continuous contract has priority.
Candidates are evaluated on all 16 dev and 16 selection runs. Subsequent source
parents can change ONLY using dev eligibility, never selection feedback.

## Multiobjective selection, fixed before scores

Against that fixture's ORIGINAL initial algorithm on the same split, require:
- no runtime errors, no lower hard-success count;
- mean visibility loss <=.01;
- position RMS diversity >=90% of initial, both across all pairs and hard-valid pairs;
- rotation diversity >=80% of initial;
- no fewer hard-valid seed pairs;
- mean preference error improves >=.01m.

Diversity uses the existing permutation-invariant within-task distance for
identical assets sharing a support. Report each component and pair counts;
diversity checks are guardrails, not proof of equal distributions. Among eligible
selection candidates choose lowest preference error, then higher hard success,
then earlier slot. Otherwise retain initial source. Also report raw candidates,
even when rejected by diversity. Apply the same guard on final test as a diagnostic,
NEVER for reselection. Report valid-pair sampling limitations explicitly.

## Calls, stopping and analysis

Start K=1: one independent Codex proposal for each M0/M1 x repair/joint = FOUR.
Same gpt-6.1-sol, medium effort, 180s CLI limit, one upstream request per reserved
slot, max 8,192 output tokens and 98,304-byte ENTIRE upstream request (includes
CLI instructions). Reserve all slots before launching up to two parallel proposal
calls. Record usage reported by service, missing usage and model ids. Input byte
cap bounds input size; do not substitute a bytes/4 estimate for measured tokens.
Maximum 16 slots if expansion occurs: 4 first-pilot +4 K2 +8 independent-repeat
K1/K2. No retry of rejected/failed proposals. Static AST pass is not evidence of
execution or algorithm improvement. Token cost is actual available usage, not a
made-up monetary charge. Global finite cap: 16 upstream calls x the per-call
input/output caps; optional rounds cannot exceed it.

Expansion gate uses SELECTION only, after all four K1 calls: at least one fixture
has a selected M0-vs-M1 preference-error gap >=.025m and a paired TASK-cluster
95% bootstrap interval excluding zero. Initial is used for a rejected candidate.
If gate fails, stop at four calls. If it passes, run K2 for all four trajectories
and a second independent model-sampling repeat with both K1 and K2, same tasks
and budgets. Never expand based on final test. No extra calls to chase a positive
result. Task-cluster uncertainty does NOT estimate model-sampling uncertainty;
one proposal per arm/fixture cannot establish meta-policy superiority.

Final comparison: fresh 12 test conditions x two seeds. Report selected AND raw
K1 candidates' quality/hard/diversity, paired preference changes, costs and failure
classes. No candidate is revised after test. Representative renders use actual
dev outputs, before/after same condition, existing Blender with idle A100 or CPU.
Do not call task search/generator edits recursive model or meta-policy improvement.
