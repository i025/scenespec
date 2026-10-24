"""Construct layouts using reversible pushes and ordinary walking connectivity."""


def layout(packet):
    w, h = packet['width'], packet['depth']
    sample = packet['sample']
    obstacles = sample[:-3]
    blocked = {y * w + x for x, y in obstacles}
    cells = [i for i in range(w * h) if i not in blocked]
    free = set(cells)
    directions = ((1, 0), (0, 1), (-1, 0), (0, -1))

    salt = 2166136261
    for j, (x, y) in enumerate(sample):
        salt = ((salt ^ ((x + 1) * 97 + (y + 1) * 193 + j * 389))
                * 16777619) & 0xffffffff

    def noise(*values):
        z = salt
        for v in values:
            z = ((z ^ (v + 0x9e3779b9)) * 1664525 + 1013904223) & 0xffffffff
            z ^= z >> 16
        return z & 0xffff

    steps = {}
    neighbors = {}
    for p in cells:
        x, y = p % w, p // w
        row = []
        for dx, dy in directions:
            xx, yy = x + dx, y + dy
            q = yy * w + xx
            row.append(q if 0 <= xx < w and 0 <= yy < h and q in free else -1)
        steps[p] = row
        neighbors[p] = [q for q in row if q >= 0]

    def distance(a, b):
        return abs(a % w - b % w) + abs(a // w - b // w)

    # These caches describe walking on a fixed floor with one occupied cell.
    # They contain no crate transitions or joint-state search frontier.
    walking = {}

    def reachable(crate, start):
        cache = walking.setdefault(crate, {})
        if start in cache:
            return cache[start]
        queue = [start]
        mask = 1 << start
        for p in queue:
            for q in neighbors[p]:
                bit = 1 << q
                if q != crate and not mask & bit:
                    mask |= bit
                    queue.append(q)
        for p in queue:
            cache[p] = mask
        return mask

    # Anchor a construction with two straight or perpendicular reverse pulls.
    # A perpendicular scaffold also needs a walking connection around its elbow.
    # No complete generated scene is submitted to a solver or rejected.
    best = None
    best_rank = None
    prefer_bend = (salt >> 7) % 4 != 0
    for elbow in cells:
        for d1 in range(4):
            goal = steps[elbow][(d1 + 2) % 4]
            first_stand = steps[elbow][d1]
            if goal < 0 or first_stand < 0:
                continue
            region = None
            for d2 in (d1, (d1 + 1) % 4, (d1 + 3) % 4):
                crate = steps[elbow][d2]
                if crate < 0:
                    continue
                agent = steps[crate][d2]
                if agent < 0:
                    continue
                if d2 != d1:
                    if region is None:
                        region = reachable(elbow, first_stand)
                    if not (region >> crate) & 1:
                        continue
                size = reachable(crate, agent).bit_count()
                bend = d1 != d2
                # Broadly favor roomy scaffolds, with sample-dependent variety.
                rank = (size // 5,
                        noise(goal, elbow, crate) +
                        (18000 if bend == prefer_bend else 0) +
                        1600 * len(neighbors[crate]))
                if best_rank is None or rank > best_rank:
                    best_rank = rank
                    best = (goal, elbow, crate, agent, d1, d2)

    if best is None:
        # Extremely fragmented floors may admit no two-push scaffold at all.
        # Preserve the geometry and distance contract even on these floors.
        pair = max(((a, b) for a in cells for b in cells
                    if distance(a, b) >= 2),
                   key=lambda ab: (distance(*ab), noise(*ab)))
        crate, goal = pair
        agent = max((p for p in cells if p != crate and p != goal),
                    key=lambda p: (len(neighbors[p]), noise(p, crate, goal)))
    else:
        goal, elbow, crate, agent, d1, last_direction = best
        visits = {goal: 1, elbow: 1, crate: 1}
        used_edges = {}
        turns = int(d1 != last_direction)
        chosen = (crate, agent)
        chosen_rank = None
        limit = 2 * (w + h) + 8
        target = max(3, (w + h) // 2 - 1 + salt % 3)

        # Follow one constructive trajectory. Every step is a reversible pull;
        # alternatives are scored locally, without branching or backtracking.
        for t in range(limit + 1):
            separation = distance(crate, goal)
            if separation >= 2:
                rank = (min(separation, target) * 12 +
                        min(turns, 4) * 5 + min(len(visits), 14),
                        noise(crate, agent, t))
                if chosen_rank is None or rank > chosen_rank:
                    chosen_rank = rank
                    chosen = (crate, agent)
            if t == limit:
                break
            region = reachable(crate, agent)
            options = []
            for d in range(4):
                q = steps[crate][d]
                if q < 0 or not (region >> q) & 1:
                    continue
                stand = steps[q][d]
                if stand < 0:
                    continue
                edge_visits = used_edges.get((crate, q), 0)
                if edge_visits >= 2:
                    continue
                mobility = sum(steps[q][k] >= 0 and
                               steps[steps[q][k]][k] >= 0
                               for k in range(4))
                score = (90 * (visits.get(q, 0) == 0)
                         - 48 * visits.get(q, 0) - 65 * edge_visits
                         + 9 * min(distance(q, goal), target)
                         + 10 * mobility
                         + 19 * (d != last_direction)
                         - 45 * (d == (last_direction + 2) % 4)
                         + noise(q, d, t) % 37)
                options.append((score, noise(crate, q, t), d, q, stand))
            if not options:
                break
            _, _, d, q, stand = max(options)
            used_edges[(crate, q)] = used_edges.get((crate, q), 0) + 1
            turns += d != last_direction
            crate, agent, last_direction = q, stand, d
            visits[crate] = visits.get(crate, 0) + 1

        crate, agent = chosen
        # Move the initial agent within its existing walking component. This
        # preserves the construction while adding useful approach navigation.
        distances = {agent: 0}
        queue = [agent]
        for p in queue:
            for q in neighbors[p]:
                if q != crate and q not in distances:
                    distances[q] = distances[p] + 1
                    queue.append(q)
        starts = [p for p in queue if p != goal]
        if starts:
            approach = 2 + noise(crate, goal) % max(3, (w + h) // 2)
            agent = max(starts,
                        key=lambda p: (min(distances[p], approach),
                                       noise(p, crate, goal)))

    def xy(p):
        return [p % w, p // w]

    return {'obstacles': obstacles, 'agent': xy(agent),
            'crate': xy(crate), 'goal': xy(goal)}
