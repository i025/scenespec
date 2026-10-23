"""Shared, immutable data contract between generator and trusted evaluator.

Coordinate convention: meters, XY ground plane, Z up. Object x/y are the
footprint centre; yaw is counter-clockwise about Z in 90-degree increments.
An object's unrotated FRONT points in local +Y.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json


@dataclass(frozen=True)
class Room:
    width: float
    depth: float
    height: float


@dataclass(frozen=True)
class Asset:
    width: float
    depth: float
    height: float


@dataclass(frozen=True)
class RequiredObject:
    id: str
    asset: str


@dataclass(frozen=True)
class Region:
    id: str
    x: float  # lower-left corner
    y: float
    width: float
    depth: float


@dataclass(frozen=True)
class Constraint:
    type: str
    subject: str = ""
    reference: str = ""
    wall: str = ""
    region: str = ""
    max_gap: float = 0.4


@dataclass(frozen=True)
class AccessPath:
    from_region: str
    to_object: str
    clearance: float = 0.2
    interaction_distance: float = 0.75


@dataclass(frozen=True)
class SceneSpec:
    scene_id: str
    room: Room
    required_objects: tuple[RequiredObject, ...]
    assets: dict[str, Asset]
    regions: tuple[Region, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    access_paths: tuple[AccessPath, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneSpec":
        scene_id = str(data["scene_id"])
        room = Room(**data["room"])
        # The asset catalog is trusted fixture data, not invented by the agent.
        assets = {k: Asset(**v) for k, v in data["assets"].items()}
        required_objects = tuple(RequiredObject(**v) for v in data["required_objects"])
        regions = tuple(Region(**v) for v in data.get("regions", []))
        constraints = tuple(Constraint(**v) for v in data.get("constraints", []))
        access_paths = tuple(AccessPath(**v) for v in data.get("access_paths", []))
        spec = cls(scene_id, room, required_objects, assets, regions, constraints, access_paths)
        spec.validate()
        return spec

    @classmethod
    def load(cls, filename: str | Path) -> "SceneSpec":
        return cls.from_dict(json.loads(Path(filename).read_text(encoding="utf-8")))

    def validate(self) -> None:
        if not (self.scene_id.strip()):
            raise ValueError("scene_id cannot be blank")
        if not (min(self.room.width, self.room.depth, self.room.height) > 0):
            raise ValueError("invalid room dimensions")
        if not (len(self.required_objects) > 0):
            raise ValueError("at least one required object is needed")
        object_ids = [v.id for v in self.required_objects]
        if not (len(set(object_ids)) == len(object_ids)):
            raise ValueError("duplicate object id")
        region_ids = [v.id for v in self.regions]
        if not (len(set(region_ids)) == len(region_ids)):
            raise ValueError("duplicate region id")
        for asset in self.assets.values():
            if not (min(asset.width, asset.depth, asset.height) > 0):
                raise ValueError("invalid asset dimensions")
        for obj in self.required_objects:
            if not (obj.id.strip()):
                raise ValueError("empty object ID")
            if not (obj.asset in self.assets):
                raise ValueError(f"unknown required asset: {obj.asset}")
        for region in self.regions:
            if not (region.width > 0 and region.depth > 0):
                raise ValueError("invalid region dimensions")
            if not ((0 <= region.x < self.room.width and 0 <= region.y < self.room.depth)):
                raise ValueError("region out of room")
            if not (region.x + region.width <= self.room.width + 1e-9):
                raise ValueError("region crosses room")
            if not (region.y + region.depth <= self.room.depth + 1e-9):
                raise ValueError("region crosses room")
        for c in self.constraints:
            if not (c.type in {"near_wall", "in_front_of", "keep_clear"}):
                raise ValueError(f"unsupported constraint: {c.type}")
            if c.type in {"near_wall", "in_front_of"}:
                if not (c.subject in object_ids):
                    raise ValueError(f"unknown subject: {c.subject}")
            if c.type == "near_wall":
                if not (c.wall in {"north", "south", "east", "west"}):
                    raise ValueError("invalid wall")
                if not (c.max_gap >= 0):
                    raise ValueError("negative gap")
            if c.type == "in_front_of":
                if not (c.reference in object_ids):
                    raise ValueError(f"unknown reference: {c.reference}")
                if not (c.reference != c.subject):
                    raise ValueError("subject cannot be own reference")
                if not (c.max_gap >= 0):
                    raise ValueError("negative gap")
            if c.type == "keep_clear":
                if not (c.region in region_ids):
                    raise ValueError(f"unknown region: {c.region}")
        for p in self.access_paths:
            if not (p.from_region in region_ids):
                raise ValueError("unknown access origin")
            if not (p.to_object in object_ids):
                raise ValueError("unknown access target")
            if not (p.clearance >= 0 and p.interaction_distance > 0):
                raise ValueError("invalid path parameters")

    def required_lookup(self) -> dict[str, RequiredObject]:
        return {v.id: v for v in self.required_objects}

    def region_lookup(self) -> dict[str, Region]:
        return {v.id: v for v in self.regions}


@dataclass(frozen=True)
class PlacedObject:
    id: str
    asset: str
    x: float
    y: float
    yaw_deg: int


@dataclass(frozen=True)
class SceneState:
    scene_id: str
    objects: tuple[PlacedObject, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneState":
        return cls(
            scene_id=str(data["scene_id"]),
            objects=tuple(PlacedObject(**v) for v in data.get("objects", [])),
        )

    @classmethod
    def load(cls, filename: str | Path) -> "SceneState":
        return cls.from_dict(json.loads(Path(filename).read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "objects": [
                {"id": v.id, "asset": v.asset, "x": v.x, "y": v.y, "yaw_deg": v.yaw_deg}
                for v in self.objects
            ],
        }

    def save(self, filename: str | Path) -> None:
        Path(filename).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
