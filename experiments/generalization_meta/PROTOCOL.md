# Frozen generalization and MetaImp@1 protocol

Registered before running the new blind set or making new model calls. Starting Git commit: b83b104.

## A: frozen generator

The selected `scene_rsi/agent/world_generator.py` is unchanged. The original is the
archived identity placement algorithm. Both receive the exact same map and random
input order, with one output and zero solvability rejection calls. Inputs condition
actor positions only on the existing geometric Manhattan-distance >=2 rule; no
oracle selects inputs. This measures placement on fixed unseen obstacle maps, not
the original generator's distribution after repeated geometric rejection.

`generalization.CONDITIONS` fixes 12 groups x 16 seeds starting at 710000:
new size/count combinations, densities 14.3%..75%, random maps, a doorway,
cross-room walls, slalom, separated components and bars. Difficulty labels describe
map designs; realized shortest-path difficulty is measured, not guaranteed. All
sizes remain within the unchanged WorldTask 4..8-cell domain. No >8-size claim.

For every map, the trusted controller searches the original transition graph from
all possible agent starts for each initial crate cell. Finding any reachable crate
goal at distance >=2 gives a witness checked by the original solver. Exhaustion
proves no legal task exists. Such maps are reported separately; neither generator
receives witnesses. Original SceneVerifier, BFS solver and PyBullet stay unchanged.

Primary metric: valid and independently solvable first outputs / feasible maps.
Secondary: all-map scores, error types, shortest actions, pushes, action-direction
changes and changes between consecutive push directions on the BFS shortest-action
solution (not minimum-push/turn paths), within-condition diversity, first-output
latency, restricted-process CPU and physics replay. No tuning after blind results.
Render the first feasible map in the predeclared `open` and `slalom` groups, before
and after, in the existing three cameras. Image choice does not depend on scores.

## B: minimal meta-level comparison

Only `scene_rsi/agent/meta_policy.py` may change at the meta level. Its pure
`layout(packet)` chooses one of three equally sized dev probes (`random`,
`barriers`, `dense`), analyzes public dev failures, chooses <=8 examples, and writes
the proposal instruction. Both the original and proposed policy execute inside
the existing restricted runner, just like generated algorithms. The outer
controller validates data outputs and owns all calls and all trusted evaluation.

Unmodifiable: model identity, outer generator contract, CLI relay and isolation,
AST audit, budgets, sample maps, original SceneSpec/Verifier/BFS/PyBullet, val
selection, and files outside the two candidate source payloads. No policy can
increase budgets, alter scores, inspect val, run shell commands or access files.

Budget registered in code: K=1 generator proposal per arm/initial generator;
one upstream call, <=8192 output tokens, <=180 s wall, same model gpt-6.1-sol.
Each optimization observes 8 common dev maps and one chosen 16-map probe;
each candidate is evaluated on those 24 dev maps and 24 private val maps.
No rejection sampling; one scene output per map. Selection requires all outputs
valid, physics replay of solved tasks, and strictly higher val solvability.

One meta-training generator (`training`, identity placement) supplies one initial
optimization trace. Codex then proposes ONE new policy source using only that
training trace and the original policy; it sees neither heldout initial sources
nor val nor task A results. Audit/restricted execution are automatic. After this,
the policy freezes before revealing two heldout initial generators (`edge`,
`spread`) to either arm. These are small independently authored experimental
fixtures, not a representative population. The unchanged original meta policy and
the proposed meta policy each optimize both fixtures with K=1. Frozen task A's
selected generator is never an initial generator, prompt hint or candidate here.

MetaImp@1 = mean over the two heldout initial generators of
`selected_val_solvable_rate - initial_val_solvable_rate`, selecting the initial
generator if the candidate is ineligible. Compare the two arms' gains and call,
token, CPU and wall costs. Also report raw proposed-candidate metrics and failures.
All calls (one training optimization + one policy proposal + four heldout
optimizations = at most SIX) and setup costs are recorded separately; equal
heldout budgets do not erase the proposed policy's extra training cost.

Val tasks/rows never enter policy packets or model requests, and heldout val is
evaluated after both arms finish proposing. No adaptation after heldout scores.
One generated policy revision and a small paired test are preparation for RSI;
even positive MetaImp@1 would not establish sustained recursive self-improvement.

All candidate code is data on the host. Reuse the validated single-process
Apptainer + seccomp runner (256 MiB, 5 CPU s, 30 wall s, no subprocess/thread,
file opens, network or GPU); do not replace it with ordinary host execution.
