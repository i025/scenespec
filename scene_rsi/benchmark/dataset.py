"""Trusted constructive benchmark generation; never expose this module to an improver.

Every task is built around a private feasible witness, then checked by the original
SceneVerifier. Split membership is by relation graph and asset catalog, not seed.
Only ``load_dataset(root, 'dev')`` results may enter a proposal workspace. The
manifest, generation seed, witnesses, val/test tasks and this source stay trusted.
Hashes detect accidental corruption; they are not a signature against a party
that can rewrite both the dataset and manifest. Protect the dataset externally.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
from typing import Any

from ..evaluator.verifier import SceneVerifier
from ..geometry import footprint, forward, projected_half_extent
from ..schema import (AccessPath, Asset, Constraint, PlacedObject, Region, Room,
                      RequiredObject, SceneSpec, SceneState)

GENERATOR_VERSION = "scene-benchmark-v1"
SPLIT_FAMILIES = {
    "dev": ("corner_chains", "paired_stations"),
    "val": ("branching_workshop", "turning_chains"),
    "test": ("shared_gallery", "cyclic_exhibit"),
}
FAMILIES = tuple(family for group in SPLIT_FAMILIES.values() for family in group)


@dataclass(frozen=True)
class Task:
    """A public specification and trusted labels; contains no witness or seed."""

    spec: SceneSpec
    family: str
    task_id: str
    spec_sha256: str


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _content(spec: SceneSpec) -> dict[str, Any]:
    """Normalize task/object/asset/region labels for a content-duplicate check."""
    value = asdict(spec)
    value.pop("scene_id")
    obj_names = {obj.id: f"o{i}" for i, obj in enumerate(spec.required_objects)}
    region_names = {region.id: f"r{i}" for i, region in enumerate(spec.regions)}
    asset_names = {name: f"a{i}" for i, name in enumerate(sorted(spec.assets))}
    value["assets"] = {asset_names[k]: v for k, v in value["assets"].items()}
    for obj in value["required_objects"]:
        obj["id"], obj["asset"] = obj_names[obj["id"]], asset_names[obj["asset"]]
    for region in value["regions"]:
        region["id"] = region_names[region["id"]]
    for constraint in value["constraints"]:
        for field, names in (("subject", obj_names), ("reference", obj_names),
                             ("region", region_names)):
            if constraint[field]:
                constraint[field] = names[constraint[field]]
    for path in value["access_paths"]:
        path["from_region"] = region_names[path["from_region"]]
        path["to_object"] = obj_names[path["to_object"]]
    return value


def _topology(spec: SceneSpec) -> list[dict[str, Any]]:
    """Label/rotation independent graph summary for the six designed families."""
    nodes = {obj.id for obj in spec.required_objects}
    edges = [(c.reference, c.subject) for c in spec.constraints if c.type == "in_front_of"]
    components = []
    while nodes:
        todo = [min(nodes)]
        component = set()
        while todo:
            node = todo.pop()
            if node in component:
                continue
            component.add(node)
            todo.extend(b if a == node else a for a, b in edges if a == node or b == node)
        nodes -= component
        degrees = sorted((sum(b == n for a, b in edges), sum(a == n for a, b in edges))
                         for n in component)
        components.append({"nodes": len(component), "edges": sum(a in component for a, b in edges),
                           "degrees": degrees})
    return sorted(components, key=lambda item: canonical_json(item))


class _Builder:
    def __init__(self, family: str, rng: random.Random):
        self.family, self.rng = family, rng
        self.width = round(10.0 + rng.random() * .8, 6)
        self.depth = round(10.0 + rng.random() * .8, 6)
        self.assets: dict[str, Asset] = {}
        self.objects: dict[str, PlacedObject] = {}
        self.constraints: list[Constraint] = []

    def asset(self, role: str, width: float, depth: float, height: float) -> str:
        name = f"{self.family}_{role}"
        scale = self.rng.uniform(.94, 1.06)
        self.assets[name] = Asset(round(width * scale, 6), round(depth * scale, 6),
                                  round(height * scale, 6))
        return name

    def add(self, id_: str, asset: str, x: float, y: float, yaw: int) -> PlacedObject:
        obj = PlacedObject(id_, asset, round(x, 6), round(y, 6), yaw)
        self.objects[id_] = obj
        return obj

    def wall(self, id_: str, wall: str, slack: float = .04) -> None:
        x0, y0, x1, y1 = footprint(self.objects[id_], self.assets[self.objects[id_].asset])
        gap = {"north": self.depth - y1, "south": y0,
               "east": self.width - x1, "west": x0}[wall]
        self.constraints.append(Constraint("near_wall", subject=id_, wall=wall,
                                           max_gap=round(gap + slack, 6)))

    def corner(self, id_: str, asset: str, corner: str, yaw: int) -> PlacedObject:
        proto = PlacedObject(id_, asset, 0, 0, yaw)
        x0, y0, x1, y1 = footprint(proto, self.assets[asset])
        gap = self.rng.uniform(.06, .12)
        x = (self.width - x1 - gap) if "e" in corner else x1 + gap
        y = (self.depth - y1 - gap) if "n" in corner else y1 + gap
        obj = self.add(id_, asset, x, y, yaw)
        self.wall(id_, "east" if "e" in corner else "west")
        self.wall(id_, "north" if "n" in corner else "south")
        return obj

    def front(self, id_: str, asset: str, reference: str, yaw: int | None = None,
              lateral: float = 0, gap: float | None = None) -> PlacedObject:
        ref = self.objects[reference]
        yaw = ref.yaw_deg if yaw is None else yaw
        fwd = forward(ref.yaw_deg)
        ref_rect = footprint(ref, self.assets[ref.asset])
        proto = PlacedObject(id_, asset, 0, 0, yaw)
        half = projected_half_extent(ref_rect, fwd) + projected_half_extent(footprint(proto, self.assets[asset]), fwd)
        gap = self.rng.uniform(.18, .28) if gap is None else gap
        obj = self.add(id_, asset, ref.x + fwd[0] * (half + gap) + fwd[1] * lateral,
                       ref.y + fwd[1] * (half + gap) - fwd[0] * lateral, yaw)
        self.relation(id_, reference, gap + .05)
        return obj

    def relation(self, subject: str, reference: str, max_gap: float) -> None:
        self.constraints.append(Constraint("in_front_of", subject=subject,
                                           reference=reference, max_gap=round(max_gap, 6)))


def _rotate(spec: SceneSpec, witness: SceneState, turns: int) -> tuple[SceneSpec, SceneState]:
    walls = {"north": "west", "west": "south", "south": "east", "east": "north"}
    for _ in range(turns):
        width, depth = spec.room.width, spec.room.depth
        objects = tuple(replace(obj, x=round(depth - obj.y, 6), y=obj.x,
                                yaw_deg=(obj.yaw_deg + 90) % 360) for obj in witness.objects)
        regions = tuple(replace(region, x=round(depth - region.y - region.depth, 6),
                                y=region.x, width=region.depth, depth=region.width)
                        for region in spec.regions)
        constraints = tuple(replace(c, wall=walls[c.wall]) if c.type == "near_wall" else c
                            for c in spec.constraints)
        spec = replace(spec, room=Room(depth, width, spec.room.height), regions=regions,
                       constraints=constraints)
        witness = replace(witness, objects=objects)
    return spec, witness


def generate_task(family: str, seed: int, index: int) -> tuple[SceneSpec, SceneState]:
    """Construct a verified legal instance; the returned witness is trusted-only.

    Families use seven to ten objects. The held-out families contain converging
    references and directed cycles absent from both development and validation.
    Geometry and global quarter-turns vary deterministically with seed and index.
    """
    if family not in FAMILIES:
        raise ValueError(f"unknown family: {family}")
    if type(seed) is not int or type(index) is not int or index < 0:
        raise ValueError("seed must be an integer and index a nonnegative integer")
    key = _digest([GENERATOR_VERSION, family, seed, index])
    b = _Builder(family, random.Random(int(key, 16)))
    # Catalogs differ in dimensions and semantic asset combinations, not labels alone.
    dims = {
        "corner_chains": ((.9, .65, 1.1), (.65, .55, .6), (.5, .45, .45)),
        "paired_stations": ((1.1, .7, .75), (.55, .65, .9), (.65, .4, 1.6)),
        "branching_workshop": ((2.25, .65, .9), (.48, .55, .55), (.7, .5, 1.5)),
        "turning_chains": ((.75, .8, 1.2), (.6, .7, .8), (.8, .55, .6)),
        "shared_gallery": ((.8, .65, 1.4), (1.9, .55, .6), (.55, .7, .85)),
        "cyclic_exhibit": ((.65, .65, 1.3), (.55, .75, .8), (.85, .55, 1.7)),
    }[family]
    roles = {
        "corner_chains": ("cabinet", "stool", "pedestal"),
        "paired_stations": ("desk", "chair", "shelf"),
        "branching_workshop": ("workbench", "tool_cart", "locker"),
        "turning_chains": ("display", "seat", "console"),
        "shared_gallery": ("plinth", "bench", "kiosk"),
        "cyclic_exhibit": ("sculpture", "sign", "case"),
    }[family]
    a, child, spare = [b.asset(role, *dim) for role, dim in zip(roles, dims)]
    if family != "cyclic_exhibit":
        b.corner("nw", a, "nw", 180)
        b.corner("ne", spare, "ne", 180)
        b.corner("sw", spare, "sw", 0)
        b.corner("se", spare, "se", 0)
    if family == "corner_chains":
        b.front("nw_mid", child, "nw")
        b.front("nw_tip", spare, "nw_mid")
        # The stool is wider than the small corner pedestal: shift inward.
        b.front("ne_tip", child, "ne", lateral=.16)
    elif family == "paired_stations":
        for corner in ("nw", "ne", "sw", "se"):
            b.front(corner + "_seat", child, corner)
    elif family == "branching_workshop":
        for i, offset in enumerate((-.68, 0, .68)):
            b.front(f"cart_{i}", child, "nw", lateral=offset, gap=.23)
        b.front("cart_extension", spare, "cart_1")
        b.front("ne_cart", child, "ne")
    elif family == "turning_chains":
        b.front("nw_turn", child, "nw", yaw=270)
        b.front("nw_cross", spare, "nw_turn", yaw=180)
        b.front("nw_end", child, "nw_cross")
        b.front("se_turn", child, "se", yaw=90)
        b.front("se_end", a, "se_turn")
    elif family == "shared_gallery":
        nw = b.objects["nw"]
        other = b.add("parallel_plinth", a, nw.x + 1.3, nw.y, 180)
        b.wall(other.id, "north")
        shared = b.front("shared_bench", child, "nw", lateral=-.65, gap=.24)
        b.relation(shared.id, other.id, .29)
        b.front("gallery_kiosk", spare, shared.id)
        b.front("ne_sign", spare, "ne")
    elif family == "cyclic_exhibit":
        b.corner("ne", spare, "ne", 90)
        b.corner("sw", spare, "sw", 270)
        for prefix, x, y in (("upper", 1.2, b.depth - 2.7),
                             ("lower", b.width - 2.7, 1.2)):
            for i, (dx, dy, yaw) in enumerate(((0, 0, 270), (1.5, 0, 0),
                                               (1.5, 1.5, 90), (0, 1.5, 180))):
                b.add(f"{prefix}_{i}", a if i % 2 == 0 else child, x + dx, y + dy, yaw)
            for i in range(4):
                b.relation(f"{prefix}_{(i + 1) % 4}", f"{prefix}_{i}", 1.05)
            b.wall(f"{prefix}_0", "west" if prefix == "upper" else "south", slack=.05)
    task_id = f"{family}_{index:04d}_{key[:10]}"
    # Two crossing, genuinely empty passage regions plus an entrance patch.
    regions = (Region("entry", round(b.width / 2 - .4, 6), .2, .8, .7),
               Region("aisle_ns", round(b.width / 2 - .6, 6), 0, 1.2, b.depth),
               Region("aisle_ew", 0, round(b.depth / 2 - .6, 6), b.width, 1.2))
    constraints = tuple(b.constraints) + tuple(Constraint("keep_clear", region=r.id) for r in regions)
    targets = ("nw_tip", "ne_tip") if family == "corner_chains" else {
        "paired_stations": ("nw_seat", "se_seat"),
        "branching_workshop": ("cart_extension", "ne_cart"),
        "turning_chains": ("nw_end", "se_end"),
        "shared_gallery": ("gallery_kiosk", "ne_sign"),
        "cyclic_exhibit": ("upper_1", "lower_2"),
    }[family]
    paths = tuple(AccessPath("entry", target, clearance=.15, interaction_distance=.65) for target in targets)
    spec = SceneSpec(task_id, Room(b.width, b.depth, 3.0),
                     tuple(RequiredObject(obj.id, obj.asset) for obj in b.objects.values()),
                     b.assets, regions, constraints, paths)
    witness = SceneState(task_id, tuple(b.objects.values()))
    spec, witness = _rotate(spec, witness, b.rng.randrange(4))
    spec.validate()
    result = SceneVerifier().verify(spec, witness)
    if not result["valid"]:
        raise RuntimeError(f"constructive witness failed for {task_id}: {result['failures']}")
    return spec, witness


def build_dataset(out: Path, seed: int = 20261023, per_family: int = 4) -> dict[str, Any]:
    """Create a new private dataset directory, refusing any existing destination.

    Rebuilding with the same arguments produces byte-identical files. No clock,
    host-dependent path, or mutable Agent output participates in generation.
    """
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"dataset destination already exists: {out}")
    if type(seed) is not int or type(per_family) is not int or per_family < 1:
        raise ValueError("seed must be an integer and per_family a positive integer")
    payloads: dict[str, bytes] = {}
    tasks = []
    splits: dict[str, list[str]] = {name: [] for name in SPLIT_FAMILIES}
    content_hashes: set[str] = set()
    for split, families in SPLIT_FAMILIES.items():
        for family in families:
            for index in range(per_family):
                spec, witness = generate_task(family, seed, index)
                spec_bytes, witness_bytes = canonical_json(asdict(spec)), canonical_json(witness.to_dict())
                content_hash = _digest(_content(spec))
                if content_hash in content_hashes:
                    raise RuntimeError("duplicate benchmark content after normalizing identifiers")
                content_hashes.add(content_hash)
                spec_path = f"{split}/{spec.scene_id}.json"
                witness_path = f"private/witnesses/{split}/{spec.scene_id}.json"
                payloads[spec_path], payloads[witness_path] = spec_bytes, witness_bytes
                splits[split].append(spec.scene_id)
                tasks.append({"task_id": spec.scene_id, "split": split, "family": family,
                              "spec_path": spec_path, "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
                              "witness_path": witness_path, "witness_sha256": hashlib.sha256(witness_bytes).hexdigest(),
                              "content_sha256": content_hash,
                              "asset_combination_sha256": _digest(sorted(spec.assets)),
                              "relation_topology_sha256": _digest(_topology(spec)),
                              "complexity": {"objects": len(spec.required_objects),
                                             "constraints": len(spec.constraints),
                                             "front_relations": sum(c.type == "in_front_of" for c in spec.constraints),
                                             "paths": len(spec.access_paths), "regions": len(spec.regions)},
                              "witness_verified": True})
    manifest: dict[str, Any] = {"format_version": 1, "generator_version": GENERATOR_VERSION,
                                "seed": seed, "per_family": per_family,
                                "families": {k: list(v) for k, v in SPLIT_FAMILIES.items()},
                                "splits": splits, "tasks": tasks,
                                "verifier": {"grid_step": .1, "semantics": "original-scene-verifier"}}
    manifest["dataset_id"] = _digest(manifest)
    payloads["manifest.json"] = canonical_json(manifest)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    for relative, payload in payloads.items():
        path = out / relative
        parent = out
        for part in path.parent.relative_to(out).parts:
            parent = parent / part
            parent.mkdir(mode=0o700, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(payload)
        path.chmod(0o600)
    return manifest


def _safe_read(root: Path, relative: str) -> bytes:
    """Accept only ordinary relative files under root, with no symlink components."""
    parsed = PurePosixPath(relative)
    if not relative or parsed.is_absolute() or ".." in parsed.parts or "\\" in relative:
        raise ValueError("unsafe dataset path")
    path = root
    for part in parsed.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("dataset symlinks are forbidden")
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError(f"missing or oversized dataset file: {relative}")
    # Final-component O_NOFOLLOW also closes the common lstat/open race.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        return stream.read(4 * 1024 * 1024 + 1)


def load_dataset(root: Path, split: str) -> list[Task]:
    """Validate the manifest and selected specifications, without reading witnesses.

    Caller controls exposure: ``test`` is for trusted final evaluation only. This
    loader is not a security boundary; sandbox mount allowlists enforce secrecy.
    """
    if split not in SPLIT_FAMILIES:
        raise ValueError("split must be dev, val, or test")
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("dataset root must be an ordinary directory")
    manifest = json.loads(_safe_read(root, "manifest.json"))
    identity = manifest.get("dataset_id")
    body = {key: value for key, value in manifest.items() if key != "dataset_id"}
    if identity != _digest(body):
        raise ValueError("dataset manifest integrity mismatch")
    if manifest.get("format_version") != 1 or manifest.get("generator_version") != GENERATOR_VERSION:
        raise ValueError("unsupported dataset version")
    if manifest.get("families") != {k: list(v) for k, v in SPLIT_FAMILIES.items()}:
        raise ValueError("unexpected family split allocation")
    tasks = manifest.get("tasks", [])
    members = manifest.get("splits", {})
    if not isinstance(tasks, list) or set(members) != set(SPLIT_FAMILIES):
        raise ValueError("invalid manifest task membership")
    seen = set()
    by_split: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLIT_FAMILIES}
    for item in tasks:
        id_ = item.get("task_id", "")
        which = item.get("split")
        if not isinstance(id_, str) or not re.fullmatch(r"[a-z0-9_]+", id_) or id_ in seen:
            raise ValueError("invalid or duplicate task id")
        if which not in SPLIT_FAMILIES or item.get("family") not in SPLIT_FAMILIES[which]:
            raise ValueError("invalid task family allocation")
        if item.get("spec_path") != f"{which}/{id_}.json":
            raise ValueError("unexpected spec path")
        if item.get("witness_path") != f"private/witnesses/{which}/{id_}.json":
            raise ValueError("unexpected witness path")
        seen.add(id_)
        by_split[which].append(item)
    for name in SPLIT_FAMILIES:
        if not by_split[name] or members[name] != [item["task_id"] for item in by_split[name]]:
            raise ValueError("empty, duplicated, or inconsistent split membership")
    result = []
    for item in by_split[split]:
        data = _safe_read(root, item["spec_path"])
        if hashlib.sha256(data).hexdigest() != item["spec_sha256"]:
            raise ValueError(f"spec integrity mismatch: {item['task_id']}")
        spec = SceneSpec.from_dict(json.loads(data))
        if spec.scene_id != item["task_id"] or _digest(_content(spec)) != item["content_sha256"]:
            raise ValueError("task identity/content mismatch")
        if _digest(_topology(spec)) != item["relation_topology_sha256"] or _digest(sorted(spec.assets)) != item["asset_combination_sha256"]:
            raise ValueError("task structure metadata mismatch")
        result.append(Task(spec, item["family"], item["task_id"], item["spec_sha256"]))
    return result
