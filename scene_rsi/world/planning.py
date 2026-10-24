"""An exact finite-state oracle and a deliberately limited fixed test agent."""
from collections import deque
from .task import DIRECTIONS, transition


def solve(task, max_nodes=100000, start=None):
    start = task.start if start is None else start
    frontier, parents = deque([start]), {start: None}
    while frontier:
        state = frontier.popleft()
        if state[1] == task.goal:
            actions = []
            while parents[state] is not None:
                state, action = parents[state]
                actions.append(action)
            return {"status": "solvable", "actions": actions[::-1], "explored": len(parents)}
        for action in DIRECTIONS:
            following, event = transition(task, state, action)
            if event != "collision" and following not in parents:
                if len(parents) >= max_nodes:
                    return {"status": "unknown", "actions": None, "explored": len(parents)}
                parents[following] = state, action
                frontier.append(following)
    return {"status": "unsolvable", "actions": None, "explored": len(parents)}


def walk(task, start, target, crate):
    frontier, paths = deque([start]), {start: []}
    while frontier:
        cell = frontier.popleft()
        if cell == target:
            return paths[cell]
        for action, (dx, dy) in DIRECTIONS.items():
            following = cell[0] + dx, cell[1] + dy
            if following != crate and task.free(following) and following not in paths:
                paths[following] = paths[cell] + [action]
                frontier.append(following)
    return None


class FixedPushAgent:
    """Navigate to a push that reduces Manhattan distance; never search joint states."""
    def next_actions(self, task, state):
        agent, crate = state
        distance = lambda cell: abs(cell[0] - task.goal[0]) + abs(cell[1] - task.goal[1])
        options = []
        for action, (dx, dy) in DIRECTIONS.items():
            ahead, behind = (crate[0] + dx, crate[1] + dy), (crate[0] - dx, crate[1] - dy)
            if not task.free(ahead) or distance(ahead) >= distance(crate):
                continue
            path = walk(task, agent, behind, crate)
            if path is not None:
                options.append(path + [action])
        return min(options, key=len) if options else []


def fixed_plan(task, start=None, max_steps=80):
    state, actions = task.start if start is None else start, []
    while state[1] != task.goal and len(actions) < max_steps:
        segment = FixedPushAgent().next_actions(task, state)
        if not segment:
            break
        for action in segment[:max_steps-len(actions)]:
            state, _ = transition(task, state, action)
            actions.append(action)
    return actions
