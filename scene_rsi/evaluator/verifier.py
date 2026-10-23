"""Trusted scene evaluator. Keep outside the self-editing Agent worktree.

No model calls; deterministic geometry/constraints and grid-based reachability.
A score is diagnostic; passing all hard checks requires zero failures.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil, isfinite
from typing import Any

from ..geometry import (
    dist_point_rect, footprint, forward, intersects,
    projected_half_extent, rect_region, right,
)
from ..schema import AccessPath, Constraint, PlacedObject, SceneSpec, SceneState


@dataclass(frozen=True)
class Failure:
    type: str
    message: str
    objects: tuple[str, ...] = ()
    constraint_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.type, "message": self.message}
        if self.objects:
            result["objects"] = list(self.objects)
        if self.constraint_index is not None:
            result["constraint_index"] = self.constraint_index
        return result


class SceneVerifier:
    def __init__(self, grid_step: float = 0.1):
        if grid_step <= 0:
            raise ValueError("grid_step must be positive")
        self.grid_step = grid_step

    def verify(self, spec: SceneSpec, state: SceneState) -> dict[str, Any]:
        failures: list[Failure] = []
        lookup = spec.required_lookup()
        region_map = spec.region_lookup()
        raw_by_id = {obj.id: obj for obj in state.objects}
        present: dict[str, PlacedObject] = {}
        seen: set[str] = set()
        for obj in state.objects:
            if obj.id in seen:
                failures.append(Failure("duplicate_object", f"duplicate object id '{obj.id}'", (obj.id,)))
                continue
            seen.add(obj.id)
            if obj.id not in lookup:
                failures.append(Failure("unexpected_object", f"'{obj.id}' was not requested", (obj.id,)))
                continue
            if obj.asset != lookup[obj.id].asset:
                failures.append(Failure("wrong_asset", f"'{obj.id}' has wrong asset '{obj.asset}'", (obj.id,)))
                continue
            if obj.yaw_deg not in {0, 90, 180, 270} or not all(isfinite(v) for v in (obj.x, obj.y)):
                failures.append(Failure("invalid_transform", f"'{obj.id}' has an invalid transform", (obj.id,)))
                continue
            present[obj.id] = obj

        if state.scene_id != spec.scene_id:
            failures.append(Failure("scene_id_mismatch", "state does not match requested scene"))

        for required in spec.required_objects:
            if required.id not in present:
                failures.append(Failure("missing_object", f"required object '{required.id}' not present", (required.id,)))

        footprints = {id_: footprint(obj, spec.assets[obj.asset]) for id_, obj in present.items()}
        boundary_pass = 0
        for id_, (x0, y0, x1, y1) in footprints.items():
            if x0 >= -1e-8 and y0 >= -1e-8 and x1 <= spec.room.width + 1e-8 and y1 <= spec.room.depth + 1e-8:
                boundary_pass += 1
            else:
                failures.append(Failure("out_of_bounds", f"'{id_}' exceeds room boundaries", (id_,)))
            if spec.assets[present[id_].asset].height > spec.room.height:
                failures.append(Failure("height_violation", f"'{id_}' exceeds ceiling height", (id_,)))

        pairs = [(a, b) for i, a in enumerate(present) for b in list(present)[i + 1:]]
        collision_count = 0
        for a, b in pairs:
            if intersects(footprints[a], footprints[b]):
                collision_count += 1
                failures.append(Failure("collision", f"'{a}' intersects '{b}'", (a, b)))

        constraint_pass = 0
        relevant_constraints = 0
        for i, c in enumerate(spec.constraints):
            relevant_constraints += 1
            correct = self._check_constraint(c, present, footprints, region_map, spec.room)
            if correct:
                constraint_pass += 1
            else:
                ids = (c.subject, c.reference) if c.type == "in_front_of" else ((c.subject,) if c.subject else ())
                failures.append(Failure("constraint_violation", f"constraint #{i} ({c.type}) not satisfied", ids, i))

        path_pass = 0
        for path in spec.access_paths:
            if path.to_object in footprints and self._is_reachable(spec, footprints, path):
                path_pass += 1
            else:
                failures.append(Failure("blocked_path", f"no accessible path from '{path.from_region}' to '{path.to_object}'", (path.to_object,)))

        scores = {
            "completeness": round(len(present) / len(spec.required_objects), 6),
            "boundary": round(boundary_pass / max(len(present), 1), 6),
            "collision": round(1 - collision_count / max(len(pairs), 1), 6),
            "spatial_relations": round(constraint_pass / max(relevant_constraints, 1), 6),
            "navigability": round(path_pass / max(len(spec.access_paths), 1), 6),
        }
        # Aggregate only to compare rough baselines; hard validity is authoritative.
        scores["mean"] = round(sum(scores.values()) / len(scores), 6)
        return {
            "scene_id": spec.scene_id,
            "valid": len(failures) == 0,
            "scores": scores,
            "failures": [f.to_dict() for f in failures],
            "counts": {"required": len(spec.required_objects), "placed": len(present), "collisions": collision_count,
                       "constraints": relevant_constraints, "paths": len(spec.access_paths)},
        }

    def _check_constraint(self, c: Constraint, placed: dict[str, PlacedObject],
                          rects: dict[str, tuple[float, float, float, float]], regions: dict, room) -> bool:
        if c.type == "keep_clear":
            zone = rect_region(regions[c.region])
            return all(not intersects(r, zone) for r in rects.values())
        if c.subject not in placed:
            return False
        r = rects[c.subject]
        if c.type == "near_wall":
            # Edge-to-wall distance, not object centre distance.
            # Negative gaps are disallowed by boundary validation.
            gaps = {"north": room.depth - r[3], "south": r[1], "east": room.width - r[2], "west": r[0]}
            return -1e-8 <= gaps[c.wall] <= c.max_gap + 1e-8
        if c.type == "in_front_of":
            if c.reference not in placed:
                return False
            ref = placed[c.reference]
            sub = placed[c.subject]
            fwd, lat = forward(ref.yaw_deg), right(ref.yaw_deg)
            dx, dy = sub.x - ref.x, sub.y - ref.y
            gap = dx * fwd[0] + dy * fwd[1] - projected_half_extent(rects[c.reference], fwd) - projected_half_extent(r, fwd)
            offset = abs(dx * lat[0] + dy * lat[1])
            lateral_overlap = projected_half_extent(rects[c.reference], lat) + projected_half_extent(r, lat)
            return -1e-8 <= gap <= c.max_gap + 1e-8 and offset <= lateral_overlap + 1e-8
        raise ValueError(f"Unknown constraint type: {c.type}")

    def _is_reachable(self, spec: SceneSpec, rects: dict, path: AccessPath) -> bool:
        """4-connected grid BFS from door centre to a free cell adjacent to target.

        Conservative occupancy: all obstacles inflated by the person's clearance.
        Note this is an abstract navigability proxy, not a physics simulation.
        """
        region = spec.region_lookup()[path.from_region]
        target = rects[path.to_object]
        obstacles = list(rects.values())
        n_x = max(1, ceil(spec.room.width / self.grid_step))
        n_y = max(1, ceil(spec.room.depth / self.grid_step))

        def xy(cell: tuple[int, int]) -> tuple[float, float]:
            return ((cell[0] + 0.5) * spec.room.width / n_x,
                    (cell[1] + 0.5) * spec.room.depth / n_y)

        def can_stand(cell: tuple[int, int]) -> bool:
            x, y = xy(cell)
            c = path.clearance
            if not (c <= x <= spec.room.width - c and c <= y <= spec.room.depth - c):
                return False
            return not any(rect[0] - c < x < rect[2] + c and rect[1] - c < y < rect[3] + c for rect in obstacles)

        sx = region.x + region.width / 2
        sy = region.y + region.depth / 2
        start = (min(n_x - 1, int(sx * n_x / spec.room.width)),
                 min(n_y - 1, int(sy * n_y / spec.room.depth)))
        if not can_stand(start):
            return False
        queue = deque([start])
        visited = {start}
        while queue:
            cell = queue.popleft()
            x, y = xy(cell)
            if dist_point_rect(x, y, target) <= path.interaction_distance + 1e-9:
                return True
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                new = (cell[0] + dx, cell[1] + dy)
                if 0 <= new[0] < n_x and 0 <= new[1] < n_y and new not in visited and can_stand(new):
                    visited.add(new)
                    queue.append(new)
        return False

