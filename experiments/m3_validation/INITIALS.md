# Predeclared initial generators

These two generators are new initial conditions for the frozen M2/M3 comparison.
They use the existing `layout(packet)` interface and leave the external 24-query
budget, assets, required objects, tasks, cameras and scoring unchanged. They read
only the public task packet and their own query history. No reference scene is
used. Final selection uses the existing per-scene `optimizer_loss`; it is not
treated as a distribution-level multiobjective score.

## Sequential projection

`initial_sequential_projection.py` independently samples continuous positions,
retains one seed-specific set of orientations throughout the query budget, and
makes one ordered half projection towards each preferred relation distance.
Floor poses are clamped after projection without revisiting the relation.
Supported objects are sampled in separated support-local bands.

The intended weakness is incomplete, order-dependent constraint coupling: a
projection can be undone by boundary clamping, later relations are not reconciled,
the projection uses planar distance, and the orientations cannot respond to
collisions or visibility evidence. Unlike `coordinate_walk`, this is a constructive
placement/projection pass rather than local pose perturbation; unlike
`whole_restart`, it explicitly modifies relation geometry after sampling.
Seed-specific yaw is retained so rotation diversity is not identically zero.

## Shared shift

`initial_shared_shift.py` constructs a seed-specific support-aware skeleton and
searches with six fixed shared moves: positive/negative x translation,
positive/negative y translation, and positive/negative yaw. Every object receives
the same move; supported-object translation uses the same direction at 0.3 scale
in its support-local frame. The next move starts from the best previously measured
layout, with floor bounds clamped independently.

The intended weakness is a low-rank search space: objects cannot be independently
repositioned or rotated to resolve competing relations, occlusions or support
constraints. Shared floor translation preserves inter-object distance unless a
zone boundary clips one object. This differs from the prior object-wise walk,
independent whole-layout restarts, and the original Repair/Joint algorithms.

Both definitions were written before new private conditions, generator outcomes,
or model proposals were inspected. Baseline difficulty and execution validity
must be measured with the existing restricted runner. This document asserts
algorithmic intent, not empirical success or discrimination.
