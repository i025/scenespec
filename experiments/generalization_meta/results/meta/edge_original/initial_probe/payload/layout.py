"""Heldout fixture: prefer boundary crate placements, keep sampled actors otherwise."""


def layout(packet):
    width, depth, sample = packet['width'], packet['depth'], packet['sample']
    obstacles = sample[:-3]
    blocked = {tuple(c) for c in obstacles}
    free = [(x, y) for y in range(depth) for x in range(width) if (x, y) not in blocked]
    crate = min(free, key=lambda c:(min(c[0], c[1], width-1-c[0], depth-1-c[1]),
                                    abs(c[0]-sample[-2][0])+abs(c[1]-sample[-2][1])))
    goals = [c for c in free if abs(c[0]-crate[0])+abs(c[1]-crate[1]) >= 2]
    goal = min(goals, key=lambda c:abs(c[0]-sample[-1][0])+abs(c[1]-sample[-1][1]))
    agent = min((c for c in free if c not in (crate, goal)),
                key=lambda c:abs(c[0]-sample[-3][0])+abs(c[1]-sample[-3][1]))
    return {'obstacles':obstacles, 'agent':agent, 'crate':crate, 'goal':goal}
