layout(packet) is a pure META-POLICY, not a scene generator.
Edit algorithm.py, which initially contains the parent M1 policy. The user authorizes
one next-generation policy M2. Your research objective is to learn from the supplied
DEV-only failed improvement attempts. Change actual data-dependent diagnosis,
objective prioritization, probe selection and/or example selection logic. Merely
appending a diversity reminder or changing long prompt text is insufficient.
Keep the existing schema: phase choose returns exactly experiment (random/barriers/dense)
and analysis <=3000 chars. Phase propose receives source, summary, examples and returns
exactly analysis <=3000, <=8 unique in-range example_indices, prompt <=8000 chars.
The fixed coding backend later edits a GENERATOR according to the policy output.
The old parent prompt's statements about modifying only a generator are historical
instructions for that later phase; THIS authorized task modifies only the meta-policy.
Do not embed a generator implementation, observed scene coordinates or seed lists.

summary retains tasks, valid/solvable counts/rates, mean preference error and visibility.
summary.multiobjective adds success_rate, preference_error_m, diversity_position_rms_m,
diversity_rotation_deg, valid_pair_count, valid_pair_diversity_m and visibility_mean.
summary.experience contains aggregate old dev attempt measurements. Historical training
valid means a runnable candidate, solvable means passed unchanged DEV multiobjective
acceptance; these are meta-level outcomes, not hidden world/test feedback.
New generator examples retain packet, generated, valid, solvable, error, quality,
shortest_actions, plus condition_index, repeat and pair_metrics with position_rms_m,
rotation_mean_deg, preference_error_m_mean and both_hard_success. Task room dimensions
are inside packet.spec.room. No missing field should cause an exception.

The fixed probe menu uses 8 dev conditions with two seeds: common conditions 0..3,
random adds 4..7 seed 0; barriers adds both seeds of conditions 5 and 7; dense adds
both seeds of conditions 5 and 6. All receive the same measured baseline pool and
fixed cost. You may select only this menu, not create evaluations or budgets.
Frozen acceptance: no hard-success loss, no runtime errors, visibility loss <=.01,
position diversity >=90% for ALL pairs and VALID pairs, rotation >=80%, no lower
valid-pair count, preference improvement >=.01m. No changing these thresholds.
Future M0/M1/M2 share the same dev metadata and historical failure statistics.

Your new logic must cause a nontext decision change (experiment or selected example
membership) on at least one public-schema diagnostic fixture, and remain data dependent
across reliability/quality/diversity/runtime scenarios. Actual capability is assessed
later on new tasks and a new initial generator, not these interface fixtures.
Allowed imports math,collections,heapq,itertools only. Top-level functions, imports,
literal constants, docstrings. No file/network/process access, globals/nonlocal,
private attributes, eval/exec/compile/getattr or extra tools inside policy execution.
The unchanged single-process Apptainer runner limits 256MiB, 5CPU s and bounded JSON.
You cannot modify evaluator, task distribution, model, query/token budgets or outer
controller. No references, private validation/test, future initial generator or
manual generator repair recipe are provided. Edit in place and summarize changes.
