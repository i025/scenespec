"""Construct layouts from supported push motifs and reversible pulls."""


def layout(packet):
    width, depth = packet['width'], packet['depth']
    sample = packet['sample']
    obstacles = sample[:-3]
    blocked = {tuple(cell) for cell in obstacles}
    cells = [(x, y) for y in range(depth) for x in range(width)
             if (x, y) not in blocked]
    free = set(cells)
    directions = ((1, 0), (0, 1), (-1, 0), (0, -1))

    def add(cell, direction):
        return cell[0] + direction[0], cell[1] + direction[1]

    def distance(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    seed = width * 131 + depth * 977
    for index, cell in enumerate(sample):
        seed = (seed * 1664525 + 1013904223
                + (index + 1) * (cell[0] + width * cell[1] + 1)) & 0xffffffff

    def noise(*values):
        value = seed
        for item in values:
            value ^= (item + 0x9e3779b9) & 0xffffffff
            value = (value * 2246822519) & 0xffffffff
            value ^= value >> 13
        return value

    def number(cell):
        return cell[0] + width * cell[1]

    def walk(start, crate):
        distances = {start: 0}
        queue = [start]
        for cell in queue:
            for direction in directions:
                neighbor = add(cell, direction)
                if neighbor in free and neighbor != crate and neighbor not in distances:
                    distances[neighbor] = distances[cell] + 1
                    queue.append(neighbor)
        return distances

    def pull_space(cell):
        return sum(add(cell, direction) in free
                   and add(add(cell, direction), direction) in free
                   for direction in directions)

    # Identify local two-push motifs. Components only describe pedestrian
    # connectivity around a single occupied cell; there is no joint-state search.
    best = None
    best_key = None
    for middle in cells:
        labels = {}
        component = 0
        for start in cells:
            if start == middle or start in labels:
                continue
            labels[start] = component
            queue = [start]
            for cell in queue:
                for direction in directions:
                    neighbor = add(cell, direction)
                    if neighbor in free and neighbor != middle and neighbor not in labels:
                        labels[neighbor] = component
                        queue.append(neighbor)
            component += 1

        for first_index, first in enumerate(directions):
            goal = (middle[0] - first[0], middle[1] - first[1])
            first_support = add(middle, first)
            if goal not in free or first_support not in free:
                continue
            for second_index, second in enumerate(directions):
                crate = add(middle, second)
                agent = add(crate, second)
                if crate not in free or agent not in free:
                    continue
                if distance(crate, goal) < 2:
                    continue
                if labels[first_support] != labels[crate]:
                    continue
                bent = first != second
                jitter = noise(number(goal), number(middle), second_index) % 37
                score = (jitter + 7 * bent + 5 * pull_space(crate)
                         + min(labels.values(), default=0) * 0)
                key = (score, noise(number(crate), first_index, second_index))
                if best_key is None or key > best_key:
                    best_key = key
                    best = (goal, crate, agent, second)

    if best is None:
        # Some obstacle configurations admit no supported two-push motif.
        # Keep those outputs valid using a deterministic geometric placement.
        pairs = [(a, b) for a in cells for b in cells
                 if distance(a, b) >= 2]
        crate, goal = max(
            pairs,
            key=lambda pair: (pull_space(pair[0]),
                              noise(number(pair[0]), number(pair[1]))))
        reachable = walk(crate, goal)
        choices = [cell for cell in cells if cell not in (crate, goal)]
        agent = max(choices, key=lambda cell:
                    (cell in reachable, noise(number(cell), 19)))
        return {'obstacles': obstacles, 'agent': agent,
                'crate': crate, 'goal': goal}

    goal, crate, agent, previous = best
    visits = {crate: 1}
    used_edges = set()
    budget = 10 + seed % 17

    # Grow one constructive reverse trajectory. Each pull has a reachable
    # handle and a free retreat cell, so its inverse is an executable push.
    for step in range(budget):
        reachable = walk(agent, crate)
        options = []
        for index, direction in enumerate(directions):
            target = add(crate, direction)
            retreat = add(target, direction)
            if target not in reachable or retreat not in free:
                continue
            if distance(target, goal) < 2:
                continue
            if visits.get(target, 0) >= 2:
                continue
            edge = (crate, target)
            if edge in used_edges:
                continue
            novelty = 1 if target not in visits else 0
            bend = direction != previous and direction != (-previous[0], -previous[1])
            reversal = direction == (-previous[0], -previous[1])
            score = (35 * novelty + 15 * bend - 24 * reversal
                     + 4 * pull_space(target)
                     + min(reachable[target], 8)
                     + noise(step, number(target), index) % 17)
            options.append((score, index, target, retreat, direction))
        if not options:
            break
        _, _, target, retreat, direction = max(options)
        used_edges.add((crate, target))
        crate, agent, previous = target, retreat, direction
        visits[crate] = visits.get(crate, 0) + 1

    # Any cell in this walking component can reach the constructed first push.
    reachable = walk(agent, crate)
    choices = [cell for cell in reachable if cell != goal]
    agent = max(choices, key=lambda cell:
                (3 * reachable[cell] + noise(number(cell), 71) % 11,
                 noise(number(cell), 73)))
    return {'obstacles': obstacles, 'agent': agent,
            'crate': crate, 'goal': goal}
