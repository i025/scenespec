"""Adaptive research policy. Produces experiments and patch instructions only."""


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) < 1e100


def _measurements(summary):
    found = []
    pending = [('', summary, 0)]
    while pending and len(found) < 96:
        path, value, depth = pending.pop()
        if not isinstance(value, dict):
            continue
        if any(k in value for k in ('tasks', 'solvable_rate', 'valid', 'solvable', 'difficulty_counts', 'diversity_by_condition')):
            found.append((path or 'overall', value))
        if depth < 5:
            for key in sorted(value, key=str, reverse=True):
                child = value[key]
                if isinstance(child, dict):
                    pending.append((path + '/' + str(key), child, depth + 1))
        if len(pending) > 160:
            pending = pending[-160:]
    return found


def _rate(row, key):
    value = row.get(key + '_rate')
    if _number(value) and 0 <= value <= 1:
        return float(value)
    count, total = row.get(key), row.get('tasks')
    if _number(count) and _number(total) and total > 0 and 0 <= count <= total:
        return float(count) / total
    return None


def _brief(summary):
    parts = []
    for path, row in _measurements(summary)[:10]:
        values = []
        for key in ('tasks', 'valid', 'solvable', 'solvable_rate', 'shortest_actions_mean', 'pushes_mean', 'action_turns_mean', 'push_turns_mean', 'first_output_seconds_mean', 'physics_replays', 'fixed_agent_successes'):
            value = row.get(key)
            if _number(value):
                values.append(key + '=' + str(round(value, 5)))
        errors = row.get('errors')
        if isinstance(errors, dict):
            items = sorted(((str(k)[:65], v) for k, v in errors.items() if _number(v)), key=lambda x: (-x[1], x[0]))[:5]
            if items:
                values.append('errors=' + str(items))
        difficulty = row.get('difficulty_counts')
        if isinstance(difficulty, dict):
            values.append('difficulty=' + str(difficulty)[:220])
        if values:
            parts.append(path[:100] + ': ' + ', '.join(values))
    return '; '.join(parts)[:1900]


def _choose(summary):
    measurements = _measurements(summary)
    overall = None
    for path, row in measurements:
        rate = _rate(row, 'solvable')
        if rate is not None:
            overall = rate
            break
    wall, opened = [], []
    for path, row in measurements:
        rate = _rate(row, 'solvable')
        if rate is None:
            continue
        name = path.lower()
        if 'wall' in name or 'barrier' in name:
            wall.append(rate)
        if 'open' in name:
            opened.append(rate)
    invalid = any(_rate(row, 'valid') is not None and _rate(row, 'valid') < 0.95 for _, row in measurements)
    if invalid:
        experiment = 'random'
        reason = 'Validity is the first bottleneck; use the broad probe to expose ordinary contract failures before specializing.'
    elif wall and opened and sum(opened) / len(opened) - sum(wall) / len(wall) >= 0.15:
        experiment = 'barriers'
        reason = 'Observed barrier performance trails open conditions; probe connectivity and access failures.'
    elif overall is None:
        experiment = 'random'
        reason = 'No comparable aggregate reliability measurement is available; obtain broad diagnostic coverage.'
    elif overall < 0.6:
        experiment = 'random'
        reason = 'Broad solvability is weak; prioritize representative failures over a specialized stress test.'
    elif overall < 0.9:
        experiment = 'barriers'
        reason = 'Partial reliability makes constrained access a useful diagnostic hypothesis; the probe will test it.'
    else:
        experiment = 'dense'
        reason = 'Measured baseline reliability is high; stress limited free space and bounded construction cost.'
    return {'experiment': experiment, 'analysis': (reason + ' This selects one fixed-cost initial-generator probe, not a candidate evaluation. ' + _brief(summary))[:3000]}


def _features(row):
    packet = row.get('packet', {})
    if not isinstance(packet, dict):
        packet = {}
    w, h = packet.get('width'), packet.get('depth')
    shape = (w if _number(w) else None, h if _number(h) else None)
    count = packet.get('obstacles')
    if isinstance(count, list):
        count = len(count)
    density = None
    if _number(w) and _number(h) and w > 0 and h > 0 and _number(count):
        density = min(9, max(0, int(10 * count / (w * h))))
    error = str(row.get('error') or 'unspecified')[:160]
    if row.get('valid') is False:
        outcome = 'invalid:' + error
    elif row.get('solvable') is False:
        outcome = 'failure:' + error
    elif row.get('solvable') is True:
        steps = row.get('shortest_actions')
        band = 'unknown' if not _number(steps) else ('short' if steps <= 5 else 'medium' if steps <= 12 else 'long')
        outcome = 'success:' + band
    else:
        outcome = 'unknown:' + error
    return outcome, shape, density


def _select(examples):
    features = [_features(row) for row in examples]
    frequencies = {}
    for feature in features:
        frequencies[feature[0]] = frequencies.get(feature[0], 0) + 1
    selected = []
    covered_outcomes, covered_shapes, covered_densities = set(), set(), set()
    covered_pairs = set()
    while len(selected) < min(8, len(examples)):
        best, best_score = None, None
        for i, (outcome, shape, density) in enumerate(features):
            if i in selected:
                continue
            pair = (outcome, shape, density)
            score = 0.0
            if outcome not in covered_outcomes:
                score += 12
                if outcome.startswith('invalid:'):
                    score += 3
                elif outcome.startswith('failure:'):
                    score += 2
            if shape not in covered_shapes:
                score += 4
            if density is not None and density not in covered_densities:
                score += 2
            if pair not in covered_pairs:
                score += 3
            score += 1.0 / frequencies[outcome]
            # Prefer a nearby success/failure comparison when input strata match.
            for j in selected:
                other = features[j]
                if shape == other[1] and density == other[2] and outcome.split(':')[0] != other[0].split(':')[0]:
                    score += 2
                    break
            rank = (score, -i)
            if best_score is None or rank > best_score:
                best, best_score = i, rank
        selected.append(best)
        outcome, shape, density = features[best]
        covered_outcomes.add(outcome)
        covered_shapes.add(shape)
        covered_densities.add(density)
        covered_pairs.add((outcome, shape, density))
    return selected


def layout(packet):
    summary = packet.get('summary', {})
    if packet.get('phase') == 'choose':
        return _choose(summary)
    examples = packet.get('examples', [])
    if not isinstance(examples, list):
        examples = []
    examples = [row if isinstance(row, dict) else {} for row in examples]
    indices = _select(examples)
    counts = {}
    for row in examples:
        name = _features(row)[0]
        counts[name] = counts.get(name, 0) + 1
    observed = '; '.join(str(k) + '=' + str(v) for k, v in sorted(counts.items()))[:650]
    evidence = _brief(summary)
    analysis = ('Prioritize the earliest demonstrated bottleneck: contract validity, first-output solvability, then useful difficulty and diversity within runtime. Summary: ' + evidence + '. Example outcomes: ' + observed + '. Selected examples cover distinct error classes, successful difficulty bands, dimensions and input density, with matched success/failure strata where available. Input geometry is context, not evidence of generated object positions. Small dev samples support hypotheses, not generalization claims.')[:3000]
    prompt = '''Produce one complete replacement of the supplied INITIAL GENERATOR algorithm. This is a single algorithm-patch opportunity. Inspect its actual source and the trusted task contract before deciding what to change. Use the measurements and selected examples below as diagnostic evidence. Do not patch this meta-policy, controller, solver, physics, validator, evaluation tasks, budgets, or model.

Research objective: improve transferable first-output validity and solvability per unit computation across unseen initial generators and input conditions, while preserving meaningful task difficulty and output diversity. Do not optimize a handful of displayed boards. Preserve the required API, obstacle/geometry requirements, minimum distances, random-input semantics, allowed imports and execution limits exactly as specified by the trusted contract. Distinguish constraints actually supplied by the task from assumptions suggested by examples.

Use this decision process internally, then return the required complete source:
1. Trace how the current source converts packet inputs into outputs. Identify the earliest broken invariant that plausibly explains the dominant measured failures. Separate malformed output or exceptions from valid-but-unsolvable scenes. An error label alone does not establish its mechanism. Do not infer crate or agent positions from packet.sample unless the current source actually uses that mapping.
2. Compare selected failures against successful examples, especially matching dimensions and density. Identify whether the likely mechanism is boundary/deadlock placement, disconnected access, inability to reach a required interaction side, an invalid constructive transition, fragile fallback, or excessive computation. Inspect existing safeguards before adding new machinery. A strong existing construction should receive a targeted repair, rather than wholesale replacement merely because another method is familiar.
3. Choose one coherent repair with a clear invariant and bounded cost. Where appropriate, construct from a feasible terminal configuration through legal reversible operations, or maintain another local feasibility certificate. Such a certificate must respect the actual physics, required support/access cells and agent connectivity at every transition. Mere geometric proximity or connectivity without the occupied movable object is insufficient. Treat this as an option, not a mandatory algorithm or an assumption about the task's mechanics.
4. Check initialization, every update, endpoint selection, optional agent relocation and all fallbacks against the same invariant. Do not replace a failed construction with an arbitrary valid-looking configuration and claim guaranteed solvability. Handle cramped and disconnected inputs, empty candidate sets, collisions and boundary checks explicitly. If the allowed input geometry makes a guarantee impossible, preserve validity and make the best bounded contract-compliant attempt without inventing obstacles or relaxing constraints.
5. Keep computation predictable: reuse static adjacency and cheap connectivity information where helpful, bound walks and retries, and avoid combinatorial candidate enumeration or large per-state caches. Do not introduce a hidden solver, exhaustive joint-state search, external oracle or repeated solver-based rejection. Respect any stricter search restrictions in the task. A feasible constructive route does not prove shortest-path difficulty.
6. Once reliability is addressed, retain input-dependent variation in geometry, object locations and construction choices. Avoid a universal corridor, a fixed template, seed lists, hardcoded boards, condition-specific patches or a constant short solution. Use bounded diversity in feasible path length, turns and approach navigation when supported by the mechanics. Do not sacrifice reliability to chase noisy difficulty statistics. If reliability is already strong, target the demonstrated quality, diversity or runtime weakness with the smallest justified change.
7. Audit the final source for contract compliance, invariant preservation, finite loops and worst-case memory. Reason through a failure and a success from the evidence and a generic edge case. If tools are available, perform only permitted checks within the existing budget; do not assume extra evaluations exist. Do not report unrun tests or unseen validation gains.

Evidence is descriptive and limited to development inputs. The selected probe tested the initial generator, not your proposed patch. There is no validation data, frozen chosen implementation, or blind generalization result here. The training trace is evidence that a repair worked on one generator, not a reference implementation to reproduce. Do not embed memorized scenes or seeds. Return the complete generator replacement in the outer controller's required format, not individual scene edits or an experimental plan.

Observed summary: ''' + evidence + '\nExample outcome counts: ' + observed + '\nSelected example indices (zero-based): ' + str(indices) + '\nSelection emphasizes diagnostic coverage; counts above describe all supplied examples, not just the selected subset.'
    return {'analysis': analysis, 'example_indices': indices, 'prompt': prompt[:8000]}
