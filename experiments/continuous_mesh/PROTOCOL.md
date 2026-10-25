# Continuous Mesh benchmark: frozen after dev, before val/test evaluation

Base 1706a0a. This round constructs a benchmark only. Zero Codex/model calls,
no M0/M1/M2 modification and no RSI claim. Historical schemas, verifier,
solvers, tasks, assets and scores remain unchanged.

## Continuous contract and actual geometry

Reuse SceneSpec for room, required objects and immutable asset dimensions.
New output JSON: `{scene_id,objects:[{id,asset,position:[x,y,z],rotation:[r,p,y]}]}`.
Position means the actual asset envelope centre, metres; rotation means Euler
XYZ radians, all continuous. This does not change old SceneState's ground/yaw
meaning and does not introduce a compatibility layer. No pose domains, allowed
pose list, coordinate lattice or quarter-turn constraint. Fixed architecture is
copied exactly; required objects cannot disappear; asset scale and evaluation
camera cannot be returned or changed by an algorithm.

Library Meshes are the original detailed crate, cabinet and wheeled Agent.
Export all vertices/triangles and every connected, closed component. Compare
each component's volume against its convex hull (relative tolerance .001);
observed maximum <.000091. Use these convex components as a compound union,
not an asset bounding box or a whole-asset convex hull. Actual assets retain
17/51/23 components via URDF; Bullet's shape-array API silently truncates at 16.
Regression checks cover a real empty gap inside an asset AABB and solid
containment without triangle-surface intersection.

World bounds use every transformed Mesh vertex. Collision narrowphase uses
the complete compound geometry, with 3mm numerical tolerance. Support requires
an upward contact within 12mm of the specified floor/mount. Final independent
gravity simulation is 240 steps at 1/240s: <=4cm translation, <=8deg rotation,
terminal speed <=.03m/s and .15rad/s, and contact with the required support.
This is a basic rigid-body check, not a long-horizon stability certificate.
Mass is a synthetic additive uniform-component model, including overlap mass;
inertia is the envelope diagonal approximation. Fixed inclined mounts and
dividers are anchored architecture, not freely balanced furniture.

Passage uses actual-mesh narrowphase for a 1.6m-high cylinder, nominal radius
.2m. A .25m navigation grid uses .327m radius probes (half-step plus tolerance
inflation), so an admitted path clears continuous edges conservatively. This
can reject a truly passable narrow route; failure is not a proof that no
continuous path exists. Constructive references pass the same check.

Visibility is actual-mesh TinyRenderer segmentation, fixed pinhole camera,
128x128: visible target pixels divided by its isolated silhouette at the
identical pose/camera. Target must have >=20 isolated pixels and all vertices
inside the frustum; leaving the frame cannot improve the metric. No box rays,
VLM, neural training or learned perceptual score. Raster resolution is finite.
Blender uses the same first camera and two fixed room-relative extra views.

## Task distribution and privacy

Four families: inclined display, doorway, screen occlusion, paired displays.
Rooms vary continuously: width 6.8..8.8m, depth 5.4..7.2m, height 3.8m.
Two required crates are supported by fixed inclined mounts, plus a floor
cabinet and wheeled Agent. Mount tilt/yaw and positions are continuous;
changing a support-relative coordinate changes child z and full rotation.
Also require per-object floor zones, a pairwise distance interval, camera
visibility >=.55, physical rest and entry-to-goal passage. The same pair has
a preferred distance (2.2m, or 4.3m for paired displays) as a SOFT objective.

8 dev, 8 validation, 12 independent test tasks; each family has 2/2/3 tasks.
SystemRandom draws private 64-bit task seeds, separately from independent
32-bit algorithm seeds. Seeds and actual val/test packets and references are
stored ONLY outside Git. There is no public default seed which reconstructs
the hidden tasks. Public unit/smoke cases are separate known fixtures.
28/28 private reference layouts passed before baseline evaluation. Reference
outputs are never used as baseline initial states, samples or optimization
feedback, and are never rendered/published. The task builder and references
must also be excluded from any future Meta-Agent request environment.

Dev calibration: random 4/8, repair 8/8, joint 8/8. The algorithms below,
thresholds, weights and task distribution are fixed before val/test scores.
Do not tune them after test feedback. Two independent generator samples per
test task; one per dev/val. Analysis unit is the task (12 clusters), not all
24 runs as independent scenes. Validation is diagnostic, not used to choose
or revise a fourth algorithm.

## Three equal-ceiling trusted baselines

All sample continuous support-relative coordinates and yaw, and convert through
the full inclined frame to xyz/Euler output. Floor contact restricts z/tilt
physically; this is support-aware placement, not uniform floating six-DoF noise.

1. random: 24 independent complete placements, keep lowest fixed optimizer loss.
2. repair: public geometric heuristic, then resample one offending/object pose
   at a time and retain improvements. No access to reference layouts.
3. joint: same heuristic start, bounded joint updates of the interacting floor
   pair and continuous local perturbations; retain improvements.

Every run receives 24 geometry/raster queries and a 5 CPU-second stopping
threshold, checked before each query (one atomic query may overrun). Queries expose actual-mesh
collision, bounds, support gap, spatial errors and raster visibility; they do
NOT run the final gravity/navigation checks. The fixed optimizer scalar is
100*(penetration+boundary excess)+30*support gap+10*hard spatial error+
.3*preferred-distance error+2*(1-visibility)+2*(1-framing).
This scalar guides baseline search only; it is NOT benchmark correctness.
Report the quality vector separately; no weighted mean substitutes for success.

All 3 algorithms are trusted repository code, not arbitrary generated Python.
Run each in a fresh worker, 2GiB AS, 45 CPU-second total worker ceiling and
90s parent wall timeout, no visible GPU. Fresh independent MeshWorld does final
scoring after generation, so query cache/warm starts do not affect gravity.
Worker order is counterbalanced and caches are not shared between methods.
This worker is NOT a sandbox for future model-produced source: that still
requires the existing accepted restricted runner and external evaluation.

Report hard success, failure types, penetration/boundary/contact/spatial errors,
visible proportion, preferred-distance error, dynamic drift, passage length,
queries, generation CPU/wall, complete-worker CPU/RSS, and within-task diversity
between two independent seeds. Diversity excludes fixed architecture and uses
movable-object xyz RMS / room diagonal plus quaternion angular distance.
Paired uncertainty resamples the 12 task clusters, not individual runs.

## Evidence and stopping

Render the lowest-index dev task of each family using joint's actual output,
plus the lowest-index random failure if present. Fixed asset sizes and cameras,
BLEND/GLB/three views. Never substitute a reference or select test examples.
Use idle A100 if available, otherwise CPU; no renderer infrastructure changes.

Minimal discrimination criterion fixed before val/test: paired task-cluster
95% bootstrap interval excludes zero for repair minus random hard success, OR
joint reduces repair's preferred-distance error by at least .10m on average
with a strictly positive paired 95% interval, without >.05 visibility or
>5 percentage-point hard-success regression. This is a small baseline check,
not a claim that advanced algorithms or Meta-Agents can be distinguished.
Preferred distances are aspirational soft goals and may be unattainable within
some zone/room conditions; compare methods on identical tasks, not raw scores
between differently sized rooms. All HARD requirements have valid witnesses.

If all three algorithms are indistinguishable or all metrics saturate, report
that the benchmark is inadequate. No model-call escalation is permitted in this
round regardless of outcome. Hard-success ceiling between strong baselines is
reported even when continuous quality distinguishes them.
