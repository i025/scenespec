"""Small structured tasks; no executable scene descriptions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import random

from ..schema import Asset, PlacedObject, Region, RequiredObject, Room, SceneSpec, SceneState
from ..evaluator.verifier import SceneVerifier

DIRECTIONS = {"up": (0, 1), "right": (1, 0), "down": (0, -1), "left": (-1, 0)}


@dataclass(frozen=True)
class WorldTask:
    spec: SceneSpec
    initial: SceneState

    @property
    def width(self):
        return int(self.spec.room.width)

    @property
    def depth(self):
        return int(self.spec.room.depth)

    @property
    def obstacles(self):
        return frozenset((int(o.x), int(o.y)) for o in self.initial.objects if o.id.startswith("obstacle_"))

    @property
    def start(self):
        objects = {o.id: (int(o.x), int(o.y)) for o in self.initial.objects}
        return objects["agent"], objects["crate"]

    @property
    def goal(self):
        region = self.spec.region_lookup()["goal"]
        return int(region.x), int(region.y)

    def free(self, cell):
        return 0 <= cell[0] < self.width and 0 <= cell[1] < self.depth and cell not in self.obstacles

    def to_dict(self):
        return {"spec": asdict(self.spec), "initial": self.initial.to_dict()}

    @classmethod
    def from_dict(cls, data):
        task = cls(SceneSpec.from_dict(data["spec"]), SceneState.from_dict(data["initial"]))
        objects = {o.id: o for o in task.initial.objects}
        if not {"agent", "crate"} <= objects.keys() or "goal" not in task.spec.region_lookup():
            raise ValueError("world requires agent, crate and goal")
        if objects["agent"].asset != "agent" or objects["crate"].asset != "crate":
            raise ValueError("agent and crate must use their corresponding assets")
        if any(o.id not in {"agent", "crate"} and not o.id.startswith("obstacle_") for o in task.initial.objects):
            raise ValueError("other objects must be static obstacle_N objects")
        if not (4 <= task.width <= 8 and 4 <= task.depth <= 8
                and task.spec.room.width == task.width and task.spec.room.depth == task.depth
                and task.spec.room.height == 3):
            raise ValueError("world rooms have 4..8 whole cells per side")
        for obj in task.initial.objects:
            asset = task.spec.assets[obj.asset]
            if not all(math.isfinite(v) and 0 < v <= 3 for v in (asset.width, asset.depth, asset.height)):
                raise ValueError("world asset dimensions must be finite and fit the room")
            if obj.x % 1 != 0.5 or obj.y % 1 != 0.5 or obj.yaw_deg != 0 or max(asset.width, asset.depth) > 1:
                raise ValueError("world objects must fit a cell and start on cell centers with yaw zero")
        goal = task.spec.region_lookup()["goal"]
        if goal.x % 1 or goal.y % 1 or goal.width != 1 or goal.depth != 1 or not task.free(task.goal):
            raise ValueError("goal must be one unobstructed whole cell")
        if not SceneVerifier().verify(task.spec, task.initial)["valid"]:
            raise ValueError("initial layout fails the original SceneVerifier")
        return task


def transition(task: WorldTask, state, action):
    """Exact game rule shared by the executor and search: move or push, never pull."""
    if action not in DIRECTIONS:
        raise ValueError("action must be up, right, down or left")
    agent, crate = state
    dx, dy = DIRECTIONS[action]
    target = agent[0] + dx, agent[1] + dy
    if not task.free(target):
        return state, "collision"
    if target == crate:
        destination = crate[0] + dx, crate[1] + dy
        if not task.free(destination):
            return state, "collision"
        return (target, destination), "push"
    return (target, crate), "move"


@dataclass(frozen=True)
class GeneratorConfig:
    width: int = 6
    depth: int = 6
    obstacles: int = 6
    avoid_corners: bool = False
    require_solvable: bool = False

    def validate(self):
        if type(self.width) is not int or type(self.depth) is not int or not (4 <= self.width <= 8 and 4 <= self.depth <= 8):
            raise ValueError("room sides must be integers in 4..8")
        if type(self.obstacles) is not int or not 0 <= self.obstacles <= min(12, self.width * self.depth - 4):
            raise ValueError("invalid obstacle count")
        if type(self.avoid_corners) is not bool or type(self.require_solvable) is not bool:
            raise ValueError("generator options must be booleans")


def make_task(width, depth, obstacles, agent, crate, goal, scene_id="world"):
    assets = {"agent": Asset(0.56, 0.56, 0.9), "crate": Asset(0.74, 0.74, 0.74),
              "obstacle": Asset(1, 1, 1.15)}
    objects = [PlacedObject("agent", "agent", agent[0] + .5, agent[1] + .5, 0),
               PlacedObject("crate", "crate", crate[0] + .5, crate[1] + .5, 0)]
    objects += [PlacedObject(f"obstacle_{i}", "obstacle", x + .5, y + .5, 0)
                for i, (x, y) in enumerate(sorted(obstacles))]
    spec = SceneSpec(scene_id, Room(width, depth, 3), tuple(RequiredObject(o.id, o.asset) for o in objects),
                     assets, (Region("goal", *goal, 1, 1),))
    return WorldTask.from_dict({"spec": asdict(spec), "initial": SceneState(scene_id, tuple(objects)).to_dict()})


def generate(seed: int, config: GeneratorConfig = GeneratorConfig()) -> WorldTask:
    from .planning import solve
    config.validate()
    rng = random.Random(seed)
    cells = [(x, y) for y in range(config.depth) for x in range(config.width)]
    corners = {(0, 0), (0, config.depth - 1), (config.width - 1, 0), (config.width - 1, config.depth - 1)}
    for attempt in range(256):
        sample = rng.sample(cells, config.obstacles + 3)
        obstacles, agent, crate, goal = sample[:-3], *sample[-3:]
        if abs(crate[0] - goal[0]) + abs(crate[1] - goal[1]) < 2:
            continue
        if config.avoid_corners and crate in corners:
            continue
        task = make_task(config.width, config.depth, obstacles, agent, crate, goal, f"world_{seed}_{attempt}")
        if not config.require_solvable or solve(task)["status"] == "solvable":
            return task
    raise ValueError("no qualifying scene within 256 generator attempts")
