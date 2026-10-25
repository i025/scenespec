# 3D tasks and frozen probe ablation: registered before proposals

Start: 766382c. M0 (`scene_rsi/agent/meta_policy.py`) and M1
(`experiments/generalization_meta/results/meta/frozen_policy.py`) are unchanged.
The previous worlds, benchmarks, original SceneSpec/SceneState, Verifier, walk
solver, execution limits, and model relay are unchanged. No M2 is proposed here.

## Task and calibration

SceneState retains XY/yaw. A response is `{state: SceneState, elevations: {id:z}}`.
Every asset has a real library mesh, with conservative solid-box collision proxies.
The original verifier checks the ground projection; a separate trusted task checker
checks every elevated box, height, support footprint with 5 cm containment margin,
exact contact height, per-object room, joint 3D centre distances, camera visibility,
and entrance-to-destination passage. PyBullet checks penetration independently.
The original verifier has not been relaxed to allow stacked floor collisions.

Three task families: stacks; two rooms with a fixed doorway; two rooms with a
fixed screen and simultaneously visible/hidden target requirements. All include
coupled stacks and inter-object distance intervals. There are 3/4/5 supports,
6/10/13 movable objects, 8/12/18 poses per support, and descendants can follow
all parent alternatives. Heights span .7..2.2m, room height is 4.5m. Domains and
required-object order are shuffled; ids are anonymous. A hidden constructive
witness passes every checker before a task is admitted. Witnesses are NEVER
mounted in either policy or proposal containers and NEVER included in requests.

Visibility is explicitly a geometric proxy, not exact rendered segmentation:
27 interior points (.15,.5,.85 per axis), square pinhole frustum, segment/AABB
occlusion by all other objects, cutaway exterior walls. Blender's overview uses
the identical camera position, target and FOV; library mesh bevels can differ
from solid-envelope ray predictions. No VLM or new robot task is used.

Navigation uses the original walk BFS on a .5m lattice starting at (.5,.5), .2m
horizontal radius and 1.8m headroom. Input endpoints keep metre-cell meaning.
The lattice is finer than the minimum inflated divider width (.7m); a regression
test caught tunnelling at the initial 1m spacing, BEFORE any model call. A path
must exist. Support plus footprint containment
is a static stability criterion, not a learned physics or dynamic-drop test.
All requirements are hard. Exactly one output is evaluated; no external
solvability rejection. Bounded search inside an algorithm is permitted and charged.

Independent calibration seeds 2100000 + family*10000 + tier*1000 + index,
27 tasks. Initial calibration without joint distances gave original 0/27,
greedy 23/27, independent 17/27, 2,000-node search 25/27: too easy. BEFORE ANY
model call, joint-distance intervals were added (tolerance 1.5/1/.6m by tier).
Joint-distance calibration: original 0/27, greedy 1/27, independent 0/27, search 9/27.
The final calibration also uses the corrected .5m passage lattice; its exact
scores are archived in results/calibration_final.json.
Both calibrations are archived. No M0/M1 result was used to adjust tasks.
Pilot gate: simple search must solve 10%..90% of calibration tasks. It passed.
Partial outputs are reported as missing objects, not kernel/runtime failures.

## Frozen pools and equal budgets

Task seed bases: dev 2200000; selection 2300000; final test 2400000.
Width/depth tiers: dev 10x8,12x10,14x12; selection depth +1; final depth +2.
Selection has 18 tasks; final test 27. No final feedback is read until ALL
proposals finish, no selection score or item feedback goes to a policy/model.
Hidden witnesses and task builder/checker source are never given to the models.

Common dev: six tasks at index 0 (support easy/medium, rooms medium/hard,
visibility easy/hard). Each probe costs six tasks: random uses index 1 and
easy/hard of all families; barriers uses index 2, all rooms/visibility tiers;
dense uses hard tasks, indices 3/4 of all families. Every round each arm receives
six common tasks and exactly one six-task probe. All dev candidate scores use
the same tasks that its parent used. All candidates share the same selection
and final tasks. MetaImp = selected final success rate minus initial final rate.
Select by hard successes, ground validity as exact tie-breaker, earlier source
on exact ties. Continue proposals from dev-best source only; selection feedback
does not influence the next proposal. Every attempt consumes its slot.

Two unseen initial generators: greedy dependency-height order without revision;
independent stack placement without inter-stack collision coordination. Common
pure geometry helpers describe the public contract, not a hidden solution.

Four arms, tuples are (proposal policy, probe-choice policy):

| Arm | Proposal | Probe |
|---|---|---|
| M0 | frozen M0 | frozen M0 (random) |
| M0_M1probe | frozen M0 | frozen M1 |
| M1_random | frozen M1 | frozen M0 (random) |
| M1 | frozen M1 | frozen M1 |

Only the choice mechanism is swapped within each policy pair. M1's example
selection/diagnosis/prompt is NOT substituted into M0. A choice is made from
common-dev measurements BEFORE the chosen probe is evaluated. M1 can legitimately
choose random too; count actual divergent choices, not merely arm labels.

One independent CLI session per arm/fixture/round: pilot n=2 initial generators,
not repeated samples of the same arm/fixture. First wave K=1 costs eight calls.
If EVERY selected selection score is >=90%, stop. Otherwise run K=2, eight
more calls maximum. No K=3, no post-test tuning, no human proposal correction.
This small pilot cannot establish repeatability or authorize M2 by itself.

All use gpt-6.1-sol, medium reasoning, max 8,192 output tokens, 180s CLI wall,
one upstream request per reservation. At most two proposal calls overlap;
candidate executions remain serial. Actual tokens are reported when the relay
receives usage, missing usage is unknown (not zero), no monetary price assumed.

Candidate AST audit is separate from kernel isolation. The existing Apptainer
runner is required: private network/mount/PID namespaces, no home/GPU/credentials,
single process, no filesystem opens after load, 256MiB AS, 5 CPU seconds,
7s response deadline, 16KB response line, bounded stdout/stderr. No host fallback.
Eight LIVE hostile-code regression checks must pass before the pilot. The
proposal container sees only its dev request, native CLI and bounded credential-free
relay. Credentials stay with the external host broker.

Report separately: complete proposals, static pass, outputs/runtime failures,
conditional algorithm quality, selected MetaImp, per-fixture results, actual
probe choices, token/time costs. No claim of full RSI or causal superiority from
one outlier, transport failure, or extra computation. If ceiling returns, report it.
Public archives contain heldout aggregates only, not heldout states/feedback.
