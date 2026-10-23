"""Fixed deterministic Scene Agent baseline.

No calls to the trusted evaluator and no learning/self-modification. This is
intentionally simple, making future meta-improvement comparisons meaningful.
"""
from __future__ import annotations

from ..geometry import footprint, forward, intersects, projected_half_extent, rect_region, right
from ..schema import Constraint, PlacedObject, SceneSpec, SceneState


class FixedSceneAgent:
    def generate(self, spec: SceneSpec) -> SceneState:
        required = spec.required_lookup()
        placed: dict[str, PlacedObject] = {}
        zones = [rect_region(spec.region_lookup()[c.region]) for c in spec.constraints if c.type == "keep_clear"]
        near_wall = {c.subject: c for c in spec.constraints if c.type == "near_wall"}
        in_front = {c.subject: c for c in spec.constraints if c.type == "in_front_of"}

        def admissible(obj: PlacedObject) -> bool:
            rect = footprint(obj, spec.assets[obj.asset])
            if rect[0] < -1e-8 or rect[1] < -1e-8 or rect[2] > spec.room.width + 1e-8 or rect[3] > spec.room.depth + 1e-8:
                return False
            if spec.assets[obj.asset].height > spec.room.height:
                return False
            return not any(intersects(rect, o) for o in zones) and not any(
                intersects(rect, footprint(p, spec.assets[p.asset])) for p in placed.values()
            )

        def near_wall_candidates(id_: str, c: Constraint):
            asset = spec.assets[required[id_].asset]
            # Wall-facing rotations: local forward +Y points into room.
            yaw = {"north": 180, "south": 0, "west": 270, "east": 90}[c.wall]
            w, d = (asset.width, asset.depth) if yaw % 180 == 0 else (asset.depth, asset.width)
            horizontal = c.wall in {"north", "south"}
            long_axis = spec.room.width if horizontal else spec.room.depth
            offset = min(0.12, c.max_gap)
            for fraction in (0.5, 0.25, 0.75, 0.4, 0.6, 0.15, 0.85):
                pos = fraction * long_axis
                if horizontal:
                    x, y = pos, (spec.room.depth - d / 2 - offset) if c.wall == "north" else (d / 2 + offset)
                else:
                    x, y = (w / 2 + offset) if c.wall == "west" else (spec.room.width - w / 2 - offset), pos
                yield PlacedObject(id_, required[id_].asset, x, y, yaw)

        def front_candidates(id_: str, c: Constraint):
            ref = placed[c.reference]
            ref_rect = footprint(ref, spec.assets[ref.asset])
            fwd, lat = forward(ref.yaw_deg), right(ref.yaw_deg)
            # The chair looks at its reference, while the reference faces the chair.
            yaw = (ref.yaw_deg + 180) % 360
            proto = PlacedObject(id_, required[id_].asset, 0.0, 0.0, yaw)
            child_rect = footprint(proto, spec.assets[proto.asset])
            basic_distance = projected_half_extent(ref_rect, fwd) + projected_half_extent(child_rect, fwd)
            for gap in (min(0.12, c.max_gap), min(0.3, c.max_gap), c.max_gap):
                for lateral in (0.0, -0.18, 0.18, -0.35, 0.35):
                    yield PlacedObject(id_, required[id_].asset,
                        ref.x + fwd[0] * (basic_distance + gap) + lat[0] * lateral,
                        ref.y + fwd[1] * (basic_distance + gap) + lat[1] * lateral,
                        yaw)

        def free_candidates(id_: str):
            asset_id = required[id_].asset
            for fraction_x, fraction_y in (
                (0.50,0.50), (0.25,0.50), (0.75,0.50), (0.25,0.25),
                (0.75,0.25), (0.25,0.75), (0.75,0.75), (0.50,0.25),
                (0.50,0.75), (0.15,0.35), (0.85,0.65), (0.35,0.65),
                (0.65,0.35), (0.35,0.35), (0.65,0.65),
            ):
                for yaw in (0, 90):
                    yield PlacedObject(id_, asset_id, spec.room.width * fraction_x,
                                       spec.room.depth * fraction_y, yaw)

        # Resolve references first. Cycles/unsatisfiable scenes cause omissions,
        # which the independent verifier will report instead of silently passing.
        remaining = {v.id for v in spec.required_objects}
        while remaining:
            ready = [id_ for id_ in sorted(remaining) if id_ not in in_front or in_front[id_].reference in placed]
            if not ready:
                break
            ready.sort(key=lambda id_: (0 if id_ in near_wall else 1 if id_ in in_front else 2, id_))
            # Place one object, then reconsider dependencies: a desk should be
            # followed by its chair before unrelated furniture fills the room.
            id_ = ready[0]
            if id_ in in_front:
                candidates = front_candidates(id_, in_front[id_])
            elif id_ in near_wall:
                candidates = near_wall_candidates(id_, near_wall[id_])
            else:
                candidates = free_candidates(id_)
            chosen = next((candidate for candidate in candidates if admissible(candidate)), None)
            if chosen is not None:
                placed[id_] = chosen
            remaining.remove(id_)
        return SceneState(scene_id=spec.scene_id, objects=tuple(placed.values()))
