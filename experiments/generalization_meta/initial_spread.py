"""Heldout fixture: open crate neighborhood and distant goal."""


def layout(packet):
    width, depth, sample = packet['width'], packet['depth'], packet['sample']
    obstacles = sample[:-3]
    blocked = {tuple(c) for c in obstacles}
    free = [(x, y) for y in range(depth) for x in range(width) if (x, y) not in blocked]
    directions = [(0, 1), (1, 0), (0, -1), (-1, 0)]
    crate = max(free, key=lambda c:(sum((c[0]+dx, c[1]+dy) in free for dx, dy in directions),
                                    -abs(c[0]-sample[-2][0])-abs(c[1]-sample[-2][1])))
    goal = max(free, key=lambda c:abs(c[0]-crate[0])+abs(c[1]-crate[1]))
    agent = min((c for c in free if c not in (crate, goal)),
                key=lambda c:abs(c[0]-sample[-3][0])+abs(c[1]-sample[-3][1]))
    return {'obstacles':obstacles, 'agent':agent, 'crate':crate, 'goal':goal}
