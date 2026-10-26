# Two paired Repair experiments, frozen before real proposals

Base 0e30ebb. Exactly two independent paired model samples, each M0/M1 from the
same original Repair source, K=1. Four coding tasks total, no additional real smoke,
retry, K=2, Joint, M2, agents, rendering, or continuation based on outcomes.
M0/M1, gpt-6.1-sol medium, native workspace tools and original evaluators stay fixed.
Previous real capability trial is reused; rerun offline native editor and eight
candidate safety tests before starting. No host generated-Python fallback.

Each pair uses the same eight existing public dev conditions (two newly sampled
algorithm seeds), new private 8 selection/12 test conditions, two shared algorithm
seeds per condition. The two pairs have separate private draws. Verify every
reference. Sampling all private conditions precedes proposals; do not inspect
heldout candidate results or adjust anything. No former test tasks are reused.
The existing prepare/request_for functions are used unchanged, including M0/M1's
different probe and example selection. Only initial Repair is provided, never old
candidate improvements, repair hints or previous private performance.

Before inference record dev baseline metrics and freeze the EXACT existing
continuous.meta_experiment.eligible rule: no runtime errors, no lower hard count,
visibility loss <=.01, all-pair AND valid-pair position diversity >=90% of baseline,
rotation diversity >=80%, no lower valid-pair count, preference improvement >=.01m.
The same ratios and threshold apply to the private selection baseline. No tuning
against candidate or test results, no extra dev veto, no changed scoring weights.
K=1 candidate replaces the initial algorithm iff the selection rule passes;
otherwise retain Repair. Report raw candidates AND selected algorithms.
Cost is measured separately, not traded against hard correctness/diversity.

All tasks: 600s, <=6 upstream requests, <=4096 output tokens/request (<=24576 total),
<=196608 input bytes/request and <=1048576 cumulative bytes; <=400000 total charged
input+output tokens. The optional quota gate reserves serialized UTF-8 request
bytes + 16384 protocol allowance + maximum output before each request, against
remaining actual service usage. This is deliberately conservative for the frozen
text-only model transport, not an exact input tokenizer. Unknown usage blocks
another request. Record reservations and actual tokens; any exceeded usage is a
budget failure, never silently accepted. This adds a common host quota only;
native editor tools, underlying model and research strategy are unchanged.
All layouts exactly 24 Mesh/pixel queries, original candidate CPU/memory/time/output
limits and independent final MeshWorld. Same token/call/time ceilings do not imply
the two policies consume identical actual resources.

Reserve four jobs before inference. Run at most two editors concurrently, save all
patches/JSONL/versions and failures; evaluate serially. Close ALL four proposals and
selection decisions before any test evaluation. No test feedback to proposer or
reselection after test. Generate no new calls after observing quality differences.

Primary comparisons: completion, audited/runnable quality, selection acceptance,
heldout multiobjective guard, raw/selected hard success, preference, position and
valid-pair diversity, rotation, visibility, generation CPU/wall and coding tokens.
Report paired M0-minus-M1 preference differences and hard/diversity differences for
each of TWO model pairs; give mean/range across pairs. Task-cluster intervals within
a pair describe task uncertainty only, not the uncertainty of two model samples.
Do not pool 48 seed runs and call them 48 independent meta-agent trials.

M1 superiority requires consistent accepted, heldout multiobjective improvement,
not just lower error or better code delivery. With n=2, even consistent differences
are exploratory. If unsupported, stop and say so. Propose an M1->M2 design only if
these results offer useful discrimination; no actual M2 inference this round.
Hidden tasks, seeds, references and per-item selection/test records stay outside
Git. Publish only public dev evidence, coding artifacts and private aggregates.
