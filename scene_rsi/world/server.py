"""Local single-world demo: stdlib HTTP, structured actions, CPU-rendered PNG."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .task import WorldTask, GeneratorConfig, generate, make_task
from .planning import solve, fixed_plan
from .engine import World


def workshop():
    return make_task(6, 6, {(0, 3), (1, 3), (4, 1), (4, 2), (4, 3)},
                     (1, 1), (2, 2), (3, 4), "workshop")


def trap():
    return make_task(6, 6, {(2, 2), (3, 2), (3, 3)}, (1, 1), (0, 0), (4, 4), "corner_trap")


class WorldServer(HTTPServer):
    def __init__(self, address, task=None, config=None):
        super().__init__(address, Handler)
        self.config = config or GeneratorConfig(require_solvable=True)
        self.config.validate()
        self.world = World(task or workshop())
        self.trajectory = [self.world.snapshot()]

    def server_close(self):
        if hasattr(self, 'world'):
            self.world.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, data, kind="application/json", status=200):
        if kind == "application/json":
            data = json.dumps(data, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path, query = urlparse(self.path).path, parse_qs(urlparse(self.path).query)
        world = self.server.world
        if path == "/":
            self.send(Path(__file__).with_name("ui.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/state":
            self.send({"task": world.task.to_dict(), **world.snapshot(),
                       "initial_solvability": solve(world.task)["status"],
                       "solvability": solve(world.task, start=world.grid)["status"]})
        elif path == "/frame":
            try:
                yaw = int(query.get("yaw", [40])[0]) % 360
                self.send(world.png(width=960, height=720, yaw=yaw), "image/png")
            except ValueError:
                self.send({"error": "invalid camera angle"}, status=400)
        elif path == "/plan":
            kind = query.get("agent", ["fixed"])[0]
            actions = (solve(world.task, start=world.grid)["actions"] if kind == "oracle"
                       else fixed_plan(world.task, start=world.grid))
            self.send({"actions": actions or [], "agent": kind})
        elif path == "/record":
            self.send({"task": world.task.to_dict(), "trajectory": self.server.trajectory,
                       "summary": world.snapshot()})
        else:
            self.send({"error": "not found"}, status=404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 65536:
                raise ValueError("request must contain at most 64 KiB of JSON")
            data = json.loads(self.rfile.read(length))
            if self.path == "/step":
                result = self.server.world.step(data["action"])
                self.server.trajectory.append(result)
                self.send(result)
            elif self.path == "/reset":
                if "task" in data:
                    task = WorldTask.from_dict(data["task"])
                elif data.get("level") == "generated":
                    task = generate(int(data.get("seed", 1)), self.server.config)
                elif data.get("level") == "trap":
                    task = trap()
                else:
                    task = workshop()
                self.server.world.close()
                self.server.world = World(task)
                self.server.trajectory = [self.server.world.snapshot()]
                self.send(self.server.world.snapshot())
            else:
                self.send({"error": "not found"}, status=404)
        except (ValueError, KeyError, TypeError) as error:
            self.send({"error": str(error)}, status=400)


def serve(port=8765, task=None, config=None):
    with WorldServer(("127.0.0.1", port), task, config) as server:
        print(f"Interactive Code World: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
