"""Small dependency-free geometric primitives (shared coordinate definitions)."""
from __future__ import annotations

from math import cos, sin, radians, hypot
from typing import TypeAlias
from .schema import Asset, PlacedObject, Region

Rect: TypeAlias = tuple[float, float, float, float]  # xmin, ymin, xmax, ymax


def footprint(obj: PlacedObject, asset: Asset) -> Rect:
    """Axis-aligned footprint (only quarter-turn rotations are supported)."""
    if obj.yaw_deg % 180 == 0:
        w, d = asset.width, asset.depth
    else:
        w, d = asset.depth, asset.width
    return (obj.x - w / 2, obj.y - d / 2, obj.x + w / 2, obj.y + d / 2)


def intersects(a: Rect, b: Rect, eps: float = 1e-8) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > eps and min(a[3], b[3]) - max(a[1], b[1]) > eps


def rect_region(region: Region) -> Rect:
    return (region.x, region.y, region.x + region.width, region.y + region.depth)


def dist_point_rect(x: float, y: float, rect: Rect) -> float:
    dx = max(rect[0] - x, x - rect[2], 0)
    dy = max(rect[1] - y, y - rect[3], 0)
    return hypot(dx, dy)


def forward(yaw_deg: int) -> tuple[int, int]:
    """World-space forward for a +Y-facing object, 90-degree CCW yaw."""
    a = radians(yaw_deg)
    return (round(-sin(a)), round(cos(a)))


def right(yaw_deg: int) -> tuple[int, int]:
    a = radians(yaw_deg)
    return (round(cos(a)), round(sin(a)))


def projected_half_extent(rect: Rect, axis: tuple[int, int]) -> float:
    return abs(axis[0]) * (rect[2] - rect[0]) / 2 + abs(axis[1]) * (rect[3] - rect[1]) / 2
