"""PyBullet collision world and CPU TinyRenderer; discrete, kinematic controls."""
from __future__ import annotations

from io import BytesIO
import math

import pybullet as p
from PIL import Image

from ..schema import PlacedObject, SceneState
from ..evaluator.verifier import SceneVerifier
from .task import transition


class World:
    def __init__(self, task):
        self.task = task
        self.client = p.connect(p.DIRECT)
        self.bodies, self.walls = {}, []
        self.grid, self.steps, self.collisions, self.pushes, self.yaw = task.start, 0, 0, 0, 0
        self._build()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if p.isConnected(self.client):
            p.disconnect(self.client)

    def _box(self, position, size, color, collision=True):
        shape = p.createCollisionShape(p.GEOM_BOX, halfExtents=[s / 2 for s in size],
                                       physicsClientId=self.client) if collision else -1
        visual = p.createVisualShape(p.GEOM_BOX, halfExtents=[s / 2 for s in size],
                                     rgbaColor=color, physicsClientId=self.client)
        return p.createMultiBody(baseMass=0, baseCollisionShapeIndex=shape, baseVisualShapeIndex=visual,
                                 basePosition=position, physicsClientId=self.client)

    def _build(self):
        w, d = self.task.width, self.task.depth
        for x in range(w):
            for y in range(d):
                c = (.91, .93, .92, 1) if (x + y) % 2 else (.85, .88, .87, 1)
                self._box((x + .5, y + .5, -.035), (.996, .996, .07), c, False)
        for pos, size in [((w/2, -.06, .14), (w+.16, .12, .28)),
                          ((-.06, d/2, .14), (.12, d+.16, .28)),
                          ((w/2, d+.06, .6), (w+.16, .12, 1.2)),
                          ((w+.06, d/2, .6), (.12, d+.16, 1.2))]:
            self.walls.append(self._box(pos, size, (.67, .73, .75, 1)))
        gx, gy = self.task.goal
        self._box((gx+.5, gy+.5, .012), (.94, .94, .024), (.16, .72, .52, 1), False)
        self._box((gx+.5, gy+.5, .027), (.62, .62, .012), (.66, .93, .77, 1), False)
        for obj in self.task.initial.objects:
            asset = self.task.spec.assets[obj.asset]
            if obj.id == "agent":
                shape = p.createCollisionShape(p.GEOM_CYLINDER, radius=asset.width/2, height=asset.height,
                                               physicsClientId=self.client)
                visual = p.createVisualShape(p.GEOM_CYLINDER, radius=asset.width/2, length=asset.height,
                                             rgbaColor=(.13, .39, .77, 1), physicsClientId=self.client)
                body = p.createMultiBody(baseMass=0, baseCollisionShapeIndex=shape, baseVisualShapeIndex=visual,
                                         basePosition=(obj.x, obj.y, asset.height/2), physicsClientId=self.client)
            else:
                color = (.98, .62, .12, 1) if obj.id == "crate" else (.28, .38, .43, 1)
                body = self._box((obj.x, obj.y, asset.height/2), (asset.width, asset.depth, asset.height), color)
            self.bodies[obj.id] = body
        self.eye = self._box((0, 0, 0), (.13, .09, .13), (.97, .98, 1, 1), False)
        self._pose("agent", self.grid[0])

    def _pose(self, name, cell):
        asset = self.task.spec.assets[name]
        p.resetBasePositionAndOrientation(self.bodies[name], (cell[0]+.5, cell[1]+.5, asset.height/2),
                                          (0, 0, 0, 1), physicsClientId=self.client)
        if name == "agent":
            angle = math.radians(self.yaw)
            p.resetBasePositionAndOrientation(self.eye, (cell[0]+.5-math.sin(angle)*.27,
                cell[1]+.5+math.cos(angle)*.27, .65), (0, 0, 0, 1), physicsClientId=self.client)

    def collision_free(self):
        p.performCollisionDetection(physicsClientId=self.client)
        moving = [self.bodies["agent"], self.bodies["crate"]]
        for body in moving:
            for other in [*self.bodies.values(), *self.walls]:
                if body != other and any(point[8] < -1e-6 for point in p.getClosestPoints(
                        body, other, distance=0, physicsClientId=self.client)):
                    return False
        return True

    def state(self):
        agent, crate = self.grid
        objects = [o for o in self.task.initial.objects if o.id not in {"agent", "crate"}]
        objects += [PlacedObject("agent", "agent", agent[0]+.5, agent[1]+.5, self.yaw),
                    PlacedObject("crate", "crate", crate[0]+.5, crate[1]+.5, 0)]
        return SceneState(self.task.spec.scene_id, tuple(objects))

    @property
    def success(self):
        return self.grid[1] == self.task.goal and SceneVerifier().verify(self.task.spec, self.state())["valid"]

    def snapshot(self):
        return {"state": self.state().to_dict(), "steps": self.steps, "collisions": self.collisions,
                "pushes": self.pushes, "success": self.success}

    def step(self, action):
        following, event = transition(self.task, self.grid, action)
        if self.success:
            return {"action": action, "event": "complete", **self.snapshot()}
        self.steps += 1
        self.yaw = {"up": 0, "right": 270, "down": 180, "left": 90}[action]
        if event != "collision":
            # Sweep the real Bullet shapes between adjacent cells; keep the game
            # state exact, without pretending to implement free-body dynamics.
            for sample in range(1, 9):
                t = sample / 8
                for i, name in enumerate(("agent", "crate")):
                    self._pose(name, tuple(a+(b-a)*t for a, b in zip(self.grid[i], following[i])))
                if not self.collision_free():
                    event = "physics_collision"
                    break
        if event in {"collision", "physics_collision"}:
            self.collisions += 1
            for name, cell in zip(("agent", "crate"), self.grid):
                self._pose(name, cell)
        else:
            self.grid = following
            self.pushes += event == "push"
        return {"action": action, "event": event, **self.snapshot()}

    def render(self, width=960, height=720, yaw=40):
        target = (self.task.width/2, self.task.depth/2, .25)
        view = p.computeViewMatrixFromYawPitchRoll(target, max(self.task.width, self.task.depth)*1.38,
                                                  yaw, -53, 0, 2)
        projection = p.computeProjectionMatrixFOV(48, width/height, .1, 60)
        rgba = p.getCameraImage(width, height, view, projection, shadow=1, lightDirection=(-3, -4, 8),
                                renderer=p.ER_TINY_RENDERER, flags=p.ER_NO_SEGMENTATION_MASK,
                                physicsClientId=self.client)[2]
        data = rgba.tobytes() if hasattr(rgba, "tobytes") else bytes(rgba)
        return Image.frombytes("RGBA", (width, height), data).convert("RGB")

    def png(self, **kwargs):
        buffer = BytesIO()
        self.render(**kwargs).save(buffer, format="PNG")
        return buffer.getvalue()
