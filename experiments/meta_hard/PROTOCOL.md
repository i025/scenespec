# MetaImp difficulty experiment: registered before model proposals

Starting commit: 5261b1f. M0 is `scene_rsi/agent/meta_policy.py`; M1 is
`experiments/generalization_meta/results/meta/frozen_policy.py`. Neither program
is modified, retrained, or chosen again during this comparison. The previous
selected push generator and all old benchmark data remain unchanged.

## New indoor task, not movable-goal pushing

Use original SceneSpec/SceneState and original SceneVerifier without edits.
Generate a complete indoor furniture layout from a finite list of allowed poses.
Fixed desks/structural columns, entrance and destination must stay exactly fixed.
All furniture must be present, obey multiple wall/front relations, avoid overlaps
and keep-clear regions, and satisfy the original accessibility check.

An additional, fixed task requirement measures shortest four-neighbor navigation
from the fixed entrance to the fixed destination, at .15 m obstacle clearance.
It reuses original `world.planning.walk`, outside candidates. The shortest path
must exist AND exceed the stated minimum length. Supplied trajectories/scores
are ignored. PyBullet independently checks the returned asset boxes for overlap.
This extends the task conditions, not the semantics of the original Verifier.

Easy/medium/hard have 6/6/11 movable objects and 8/12/16 shuffled poses per object;
there are immutable target furniture and sometimes columns. Room rotations, opaque
object labels, domain distractors, relation chains and globally coupled passage
requirements prevent choosing a nearby easy goal. Hidden constructive layouts
are checked by original Verifier, original walk and PyBullet. Witnesses and task
builder source are never provided to either policy or proposal process.

The domains bound a real furniture-placement algorithm problem; they do not
provide an executable scene or identify a reference layout. Bounded constraint
search and ordinary navigation BFS inside a generator are permitted and charged
to its execution budget. The controller accepts exactly one SceneState output,
with no output-level rejection or reference-oracle feedback.

## Initial generators and splits

Freeze THREE new, human-authored initial generator fixtures, not exposed during
M1's previous training: first_fit (greedy packing without spatial relations),
wall_priority (uses just one wall per object, no relational backtracking), and
local_relations (dependency-ordered local placement, no global placement revision
or shortest-path difficulty objective). These are distinct static indoor
algorithm defects, not the old crate/goal relocation defect. All fixtures have
the same output interface, so repairing it cannot change the trusted task.

Data generation is fixed in `benchmark/meta_tasks.py`: dev seed 1200000,
selection seed 1300000, final-test seed 1400000. Unrotated size profiles are:

| tier | dev | selection | final test |
|---|---|---|---|
| easy | 7x8 | 8x8 | 7x9 |
| medium | 8x9 | 9x9 | 9x10 |
| hard | 10x10 | 10x11 | 11x11 |

Shapes are disjoint even after quarter turns. The difficulty bounds are derived
from each private constructive route before any candidate is run: route length
minus 8/2/0 steps, at least entrance-goal Manhattan distance +4. All final-test
tasks must have verified feasible witnesses; impossible tasks are a construction
error, not a generator failure.

Per proposal, observe 6 common dev tasks (two per tier) and one 6-task probe.
`random` contains two more per tier; `barriers` contains three medium/three hard;
`dense` contains six hard tasks. The probe choice is made by the unchanged policy.
All poses are newly generated; none of the old dev/val or blind maps is reused.
Selection has 12 tasks (four per tier); final test has 24 (eight per tier).
Only dev task inputs, actual generated states, dev failures and previous
dev/static failures can enter a proposal. Selection/test rows never enter a
policy packet. Public releases omit both selection and test per-task data.

## Equal-budget model experiment

3 initial generators x 2 policies x TWO independent model repetitions = 12
optimization trials. Each repetition uses a fresh ephemeral Codex CLI session,
same model gpt-6.1-sol and medium reasoning. Model randomness is not seedable by
this interface; independent samples mean separate upstream responses, not an
assertion of fixed model RNG seeds. Counterbalance arm order between repetitions.

First K=2: 24 upstream proposal slots. If EVERY trial's selected version succeeds
on all 12 selection tasks, declare selection ceiling, stop, and do not call K=3.
Otherwise BOTH policies in ALL trials receive exactly one more proposal: K=3,
at most 36 total model calls. This extension depends only on selection, never on
final-test results. Preserve results at both K=2 and K=3. No task/policy/scorer
revision after seeing these scores; if both strategies saturate, report loss of
discrimination rather than meta-level success.

Per slot: <=1 upstream inference, <=8192 output tokens, <=180 s CLI wall,
<=524288 upstream request bytes; existing credential-free private CLI workspace
and host credential relay. Two trusted proposal transports may overlap; untrusted
program execution is serial. All slots are reserved before calls. No retries or
manual candidate fixes. A failed model format/static audit consumes its slot.
The same explicit AST contract, including the ban on ALL Global/Nonlocal, is
shown to both strategies to reduce the old audit-only confound.

Per task: unchanged single-process Apptainer/seccomp runner, 256 MiB, 5 CPU s,
7 s response deadline, 30 s process lifetime, bounded stdout, no files/network/
process creation/GPU/credentials. Fresh process per task, so one slow task cannot
consume another task's allowance. No host fallback. Original evaluator, tasks,
resources and controller are never candidate-mutable. No rendering infrastructure
changes, local VLM, model training, or new isolation framework.

Before every proposal, evaluate its current parent on the 12 selected dev tasks;
evaluate an audited proposal on those SAME 12 tasks. Update the next round's
parent only for higher dev full success, breaking an exact tie by original
Verifier validity; otherwise retain the parent. This uses no selection feedback.
The unchanged policies may choose a different probe in later rounds.

Independently select from the INITIAL generator and all audited candidates using
selection full success first, original Verifier validity second, earlier source
on ties. Diagnostic improvements cannot outweigh fewer successful tasks. Store
all sources, Diffs, audit and execution results. Evaluate the selected prefix
versions and initial generators on final test ONLY after every proposal finishes;
identical selected versions are tested once. No final feedback returns to any
policy/model and no more model calls are allowed afterward.

## Metrics and interpretation

MetaImp@K = selected final-test success rate minus initial final-test success
rate. Report means and SAMPLE standard deviations across the six trials per
policy, per-fixture results (n=2), and six paired M1-minus-M0 differences.
Small n and stochastic models limit inference; do not equate a mean difference
with statistically established superiority.

Separately report (1) static/format pass counts, (2) candidate algorithm quality
on selection CONDITIONAL on passing static audit, (3) selected final-test gains,
(4) validity/path-length/constraint failure categories, (5) actual per-call and
aggregate input/output tokens, proposal time, bounded execution costs. Conditional
candidate quality uses selection, not secret final feedback; candidates within
one optimization trajectory are not independent statistical replicates.

Support an algorithm-quality advantage only if it persists beyond audit success
and appears in independent final outcomes. Report ceiling or floor effects,
quality/cost tradeoffs, selection overfit and failure examples explicitly. This
compares two frozen meta programs; it is not another policy evolution run and
does not establish sustained recursive self-improvement.
