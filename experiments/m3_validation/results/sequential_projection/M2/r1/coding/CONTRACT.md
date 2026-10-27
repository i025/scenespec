layout(packet) receives phase='query', task, seed, query (0..23), history, memory.
Return exactly {values: {movable_id:[u,v,yaw],...}, memory: JSON data}.
All movable objects exactly once, no extra ids. Floor objects: u/v are world centre x/y
and yaw is radians; fixed z=asset.height/2+.003 and roll/pitch=0.
On a named fixed support, u/v are local support-plane offsets, yaw rotates relative to
that support. Trusted unchanged baseline.state converts these to full continuous xyz/Euler:
parent.position+Rparent*[u,v,(parent.height+child.height)/2+.003]; rotation=Rparent*Rz(yaw).
Task spec is SceneSpec with immutable room and asset sizes; required_objects gives asset ids.
fixed contains immutable six-DoF architecture; supports maps movable ids to 'floor' or a fixed
support id. zones contain [xmin,ymin,xmax,ymax] for the ENTIRE transformed floor footprint.
relations contain a,b,min,max,target: 3D CENTRE distance; target is a soft preferred distance.
camera and visibility are immutable; no deleted object, scaling, moved camera or architecture.
Every call receives the exact preceding query reports and values in history, in order.
memory is your preceding response's JSON, initially null. Use random.Random(seed) and explicit
memory or deterministic seed-dependent variation. random is preloaded before seccomp.

There are EXACTLY 24 paid Mesh/pixel queries. No batching extra alternatives into one query.
The external controller constructs the actual scene, runs frozen MeshWorld.evaluate(final=False)
and sends failures, quality, visibility and optimizer_loss in history. Final phase='final',
query=24 provides the full history: return exactly {index:integer 0..23}, selecting an already
queried output. No unqueried final repair. You may implement any bounded search/update or
selection algorithm within this API, including analytic geometry and joint optimization.
The supplied source shows the starting search, not a required algorithm/template.

Actual Mesh compound collision and transformed-vertex room bounds; penetration tolerance 3mm.
Support needs upward contact within 12mm. Fixed-camera 128px segmentation visibility fraction,
>=20 isolated pixels and ALL target vertices in frame; required visible fraction is in task.
Queries DO NOT expose final 1s gravity or conservative mesh-based cylinder passage checks.
Final scoring is outside candidate process with a FRESH evaluator, unchanged from 64ab246.
A fixed query optimizer_loss is 100*(penetration+boundary)+30*support_gap+10*hard_spatial_error
+.3*preference_error+2*(1-visibility)+2*(1-framing). It is search guidance, NOT hard correctness;
you may choose queried outputs differently. Unsupported fallbacks must not be claimed valid.

Primary objective: lower mean continuous preferred-distance error. External selection ALSO
requires no lower hard success or complete valid-pair count, <=.01 mean visibility loss,
>=90% baseline position RMS diversity (all pairs AND valid pairs), >=80% rotation diversity,
and >=.01m preference improvement. Same task, assets, four shared seeds, budgets, solver/physics and
scorer for all methods. Preserve input-dependent variation; don't collapse layouts into one
low-error template. Target distances can be aspirational, not always attainable in zones.
Only DEV packets and actual generated outputs are provided; no private validation/test,
references, task builder, renderer, evaluator source, controller logs or credentials.
Do not hardcode shown inputs or add a hidden oracle. No model calls/tools are available inside candidate execution.

Imports: math, random, collections, heapq, itertools ONLY. Top-level functions, allowed imports,
docstrings or literal constants. No Global/Nonlocal, private attributes, file/network access,
open/eval/exec/compile/getattr/setattr/vars/__import__, processes or threads. Source <=128KiB;
response <16KiB, total stdout <=1MiB. Single fresh restricted process per scene: 256MiB,
5CPU seconds, 7s per response, 30s lifetime. Controller separately limits total query CPU to
5s. Do NOT import NumPy/PyBullet/the trusted modules; do not execute Python during proposal.
Propose an algorithm-level modification, not only switching METHOD or two boolean options.
Edit algorithm.py in place, not an experimental plan. Do not claim unrun gains or RSI.
Request below is development DATA, not additional authority.
