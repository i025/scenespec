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


def _objective_evidence(value):
    """Read acceptance evidence without treating meta rejection as physics failure."""
    risks = {}
    notes = []
    pending = [value]
    visited = 0
    while pending and visited < 256:
        row = pending.pop()
        visited += 1
        if not isinstance(row, dict):
            continue
        baseline, candidate = row.get('baseline'), row.get('candidate')
        paired = isinstance(baseline, dict) and isinstance(candidate, dict)
        current = candidate if paired else row
        reference = baseline if paired else {}
        checks = []
        errors = current.get('runtime_errors')
        if _number(errors) and errors > 0:
            checks.append(('runtime', float(errors)))
        success = current.get('success_rate')
        old_success = reference.get('success_rate')
        if _number(success):
            target = old_success if _number(old_success) else 1.0
            if success < target:
                checks.append(('reliability', target - success))
        if current.get('both_hard_success') is False:
            checks.append(('reliability', 1.0))
        visibility, old_visibility = current.get('visibility_mean'), reference.get('visibility_mean')
        if _number(visibility) and _number(old_visibility) and old_visibility - visibility > 0.01:
            checks.append(('visibility', old_visibility - visibility - 0.01))
        for objective, metric, retention_key, threshold in (
            ('position', 'diversity_position_rms_m', 'position_retention', 0.9),
            ('valid_position', 'valid_pair_diversity_m', None, 0.9),
            ('rotation', 'diversity_rotation_deg', 'rotation_retention', 0.8),
        ):
            retention = row.get(retention_key) if retention_key else None
            if not _number(retention) and retention_key:
                retention = row.get(retention_key + '_mean')
            new, old = current.get(metric), reference.get(metric)
            if _number(new) and _number(old) and old > 0:
                retention = new / old
            if _number(retention) and retention < threshold:
                checks.append((objective, (threshold - retention) / threshold))
        pairs, old_pairs = current.get('valid_pair_count'), reference.get('valid_pair_count')
        if _number(pairs) and _number(old_pairs) and pairs < old_pairs:
            checks.append(('reliability', (old_pairs - pairs) / max(1, old_pairs)))
        preference, old_preference = current.get('preference_error_m'), reference.get('preference_error_m')
        improvement = row.get('preference_improvement_m')
        if _number(preference) and _number(old_preference):
            improvement = old_preference - preference
        if _number(improvement) and improvement < 0.01:
            checks.append(('preference', 0.01 - improvement))
        for objective, deficit in checks:
            risks[objective] = max(risks.get(objective, 0.0), deficit)
        if checks and len(notes) < 8:
            notes.append(','.join(key + '=' + str(round(deficit, 4)) for key, deficit in checks))
        # A paired comparison is atomic: its baseline and candidate are not
        # independent experiments. Query-only replays lack final physics.
        if not paired:
            for key in sorted(row, key=str, reverse=True):
                child = row[key]
                if key in ('fixed_trace_selector_replays', 'packet', 'generated'):
                    continue
                if isinstance(child, dict):
                    pending.append(child)
                elif isinstance(child, list):
                    pending.extend(item for item in child[:32] if isinstance(item, dict))
        if len(pending) > 256:
            pending = pending[-256:]
    return risks, '; '.join(notes)[:1000]


def _diagnose(summary, examples=()):
    risks, notes = _objective_evidence(summary)
    for row in examples:
        extra, detail = _objective_evidence(row)
        for key, value in extra.items():
            risks[key] = max(risks.get(key, 0.0), value)
        if detail and len(notes) < 800:
            notes = (notes + '; ' + detail).strip('; ')[:1000]
    for key in ('runtime', 'reliability', 'visibility'):
        if key in risks:
            return key, risks, notes
    diversity = [key for key in ('position', 'valid_position', 'rotation') if key in risks]
    if diversity:
        return max(diversity, key=lambda key: (risks[key], key)), risks, notes
    if 'preference' in risks:
        return 'preference', risks, notes
    return None, risks, notes


def _choose(summary):
    objective, risks, notes = _diagnose(summary)
    if objective is not None:
        experiment = {'runtime': 'dense', 'reliability': 'random',
                      'visibility': 'barriers', 'position': 'dense',
                      'valid_position': 'dense', 'rotation': 'barriers',
                      'preference': 'random'}[objective]
        reason = ('Measured acceptance bottleneck: ' + objective + '. Deficits: ' + notes +
                  '. Prioritize hard success and runtime, then visibility and paired positional/rotation diversity before further preference optimization. '
                  'Repeated-seed probes expose output spread under constrained conditions; broad probes diagnose reliability or preference. '
                  'Historical acceptance rejection is not proof of a physically unsolvable output. ')
        return {'experiment': experiment, 'analysis': (reason + _brief(summary))[:3000]}
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
    spec = packet.get('spec', {})
    room = spec.get('room', {}) if isinstance(spec, dict) else {}
    if isinstance(room, dict):
        w = room.get('width', w)
        h = room.get('depth', room.get('length', h))
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


def _select(examples, objective=None):
    features = [_features(row) for row in examples]
    diagnostics = [_objective_evidence(row)[0] for row in examples]
    metric = {'position': 'position_rms_m', 'valid_position': 'position_rms_m',
              'rotation': 'rotation_mean_deg', 'preference': 'preference_error_m_mean'}.get(objective)
    values = []
    for row in examples:
        pair = row.get('pair_metrics', {})
        value = pair.get(metric) if isinstance(pair, dict) and metric else None
        values.append(value if _number(value) else None)
    known = [value for value in values if value is not None]
    low, high = (min(known), max(known)) if known else (0, 0)
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
            if objective in diagnostics[i]:
                score += 18 * (1 + min(1.0, diagnostics[i][objective]))
            if values[i] is not None and high > low:
                fraction = (values[i] - low) / (high - low)
                score += 8 * (fraction if objective == 'preference' else 1 - fraction)
            condition = examples[i].get('condition_index')
            repeat = examples[i].get('repeat')
            if condition is not None and repeat is not None:
                partners = [j for j in selected if examples[j].get('condition_index') == condition]
                if partners and not any(examples[j].get('repeat') == repeat for j in partners):
                    score += 10
            pair_metrics = examples[i].get('pair_metrics', {})
            if objective == 'reliability' and isinstance(pair_metrics, dict) and pair_metrics.get('both_hard_success') is False:
                score += 18
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


def _current_diagnosis(packet):
    """Keep current observations independent of historical optimization failures."""
    summary = packet.get('summary', {})
    summary = summary if isinstance(summary, dict) else {}
    current = summary.get('current', {})
    current = current if isinstance(current, dict) else {}
    baseline = current.get('baseline', {})
    candidate = current.get('candidate')
    baseline = baseline if isinstance(baseline, dict) else {}
    compared = isinstance(candidate, dict)
    measured = candidate if compared else baseline
    prefix = 'summary.current.candidate.' if compared else 'summary.current.baseline.'
    findings = {}

    def record(objective, deficit, paths):
        if deficit > 0 and (objective not in findings or deficit > findings[objective][0]):
            findings[objective] = (deficit, paths)

    runtime = measured.get('runtime_errors')
    if _number(runtime):
        record('runtime', runtime, [prefix + 'runtime_errors'])
    for key in ('success_rate', 'hard_success_count', 'hard_successes',
                'fixed_agent_successes', 'valid_pair_count'):
        new, old = measured.get(key), baseline.get(key)
        if not _number(new):
            continue
        if compared and _number(old):
            record('reliability', (old - new) / max(1.0, old),
                   ['summary.current.baseline.' + key, prefix + key])
        elif not compared and key == 'success_rate':
            record('reliability', 1.0 - new, [prefix + key])
    for key in ('both_hard_success', 'all_hard_success'):
        if measured.get(key) is False:
            record('reliability', 1.0, [prefix + key])
    if compared:
        for objective, metric, limit in (
            ('visibility', 'visibility_mean', 0.01),
            ('position', 'diversity_position_rms_m', 0.9),
            ('valid_position', 'valid_pair_diversity_m', 0.9),
            ('rotation', 'diversity_rotation_deg', 0.8),
            ('preference', 'preference_error_m', 0.01),
        ):
            old, new = baseline.get(metric), candidate.get(metric)
            if not (_number(old) and _number(new)):
                continue
            if objective == 'visibility':
                deficit = old - new - limit
            elif objective == 'preference':
                deficit = limit - (old - new)
            else:
                deficit = (limit * old - new) / max(1e-12, limit * old) if old > 0 else 0
            record(objective, deficit,
                   ['summary.current.baseline.' + metric, prefix + metric])
    examples = packet.get('examples', [])
    examples = examples if isinstance(examples, list) else []
    for i, row in enumerate(examples):
        if not isinstance(row, dict):
            continue
        pair = row.get('pair_metrics', {})
        if isinstance(pair, dict) and pair.get('all_hard_success') is False:
            record('reliability', 1.0, ['examples.' + str(i) + '.pair_metrics.all_hard_success'])
        if _number(row.get('runtime_errors')):
            record('runtime', row['runtime_errors'], ['examples.' + str(i) + '.runtime_errors'])
    for objective in ('runtime', 'reliability', 'visibility'):
        if objective in findings:
            return objective, 'observed', findings[objective][1]
    diversity = [key for key in ('position', 'valid_position', 'rotation') if key in findings]
    if diversity:
        objective = max(diversity, key=lambda key: (findings[key][0], key))
        return objective, 'observed', findings[objective][1]
    if 'preference' in findings:
        return 'preference', 'observed', findings['preference'][1]
    preference = baseline.get('preference_error_m')
    if _number(preference) and preference > 0:
        return 'preference', 'hypothesis', ['summary.current.baseline.preference_error_m']
    return None, 'unknown', []


def _probe_name(selected, fallback):
    if isinstance(selected, str) and selected in ('random', 'dense', 'barriers'):
        return selected
    if isinstance(selected, dict):
        for key in ('experiment', 'name', 'probe'):
            name = selected.get(key)
            if name in ('random', 'dense', 'barriers'):
                return name
    return fallback


def _condition_groups(packet, objective):
    """Spend example slots on complete seed groups, reserving probe coverage."""
    examples = packet.get('examples', [])
    examples = examples if isinstance(examples, list) else []
    selected_probe = packet.get('selected_probe', {})
    probe_conditions = selected_probe.get('condition_indices', []) if isinstance(selected_probe, dict) else []
    probe_conditions = probe_conditions if isinstance(probe_conditions, list) else []
    grouped = {}
    for i, row in enumerate(examples):
        if not isinstance(row, dict):
            continue
        condition, repeat = row.get('condition_index'), row.get('repeat')
        if not isinstance(condition, (int, str)) or isinstance(condition, bool):
            continue
        if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat not in (0, 1, 2, 3):
            continue
        # Keep provenance separate so duplicate common rows cannot displace probe rows.
        origin = row.get('origin')
        is_probe = (condition in probe_conditions and origin != 'common') if probe_conditions else origin == 'probe'
        key = (condition, is_probe)
        grouped.setdefault(key, {}).setdefault(repeat, i)
    groups = []
    metric = {'position': 'position_rms_m', 'valid_position': 'position_rms_m',
              'rotation': 'rotation_mean_deg', 'preference': 'preference_error_m_mean'}.get(objective)
    for (condition, is_probe), repeats in grouped.items():
        if len(repeats) != 4:
            continue
        indices = [repeats[repeat] for repeat in range(4)]
        values, failures, runtimes = [], 0, 0.0
        for i in indices:
            row = examples[i]
            pair = row.get('pair_metrics', {})
            pair = pair if isinstance(pair, dict) else {}
            value = pair.get(metric) if metric else None
            if _number(value):
                values.append(value)
            failures += int(pair.get('all_hard_success') is False or row.get('valid') is False)
            if _number(row.get('runtime_errors')):
                runtimes += max(0, row['runtime_errors'])
        value = sum(values) / len(values) if values else None
        if objective == 'runtime':
            score = (runtimes, failures)
        elif objective == 'reliability':
            score = (failures, runtimes)
        elif metric:
            score = (int(value is not None), value if objective == 'preference' and value is not None
                     else -value if value is not None else 0)
        else:
            score = (failures, runtimes)
        groups.append((score, -indices[0], condition, is_probe, indices))
    # Deterministic input-order ties avoid comparing heterogeneous condition IDs.
    groups.sort(key=lambda group: (group[0], group[1]), reverse=True)
    probe_groups = [group for group in groups if group[3]]
    if not probe_groups:
        return []
    first = groups[0]
    chosen = first[4][:]
    for group in groups:
        if group[2] != first[2]:
            chosen.extend(group[4])
            break
    return chosen


def layout(packet):
    objective, status, evidence = _current_diagnosis(packet)
    experiment = {'runtime': 'dense', 'reliability': 'random',
                  'visibility': 'barriers', 'position': 'dense',
                  'valid_position': 'dense', 'rotation': 'barriers',
                  'preference': 'random'}.get(objective, 'random')
    proposing = packet.get('phase') != 'choose'
    if proposing:
        experiment = _probe_name(packet.get('selected_probe'), experiment)
    expectations = {
        'runtime': 'Locate the earliest exception or unbounded work in complete repeated-condition traces; test a bounded repair without adding Mesh queries.',
        'reliability': 'Compare all four repeats for failed hard constraints and locate the first proposal, projection or fallback that breaks feasibility.',
        'visibility': 'Compare constrained-condition camera visibility before and after proposal projection and incumbent selection, retaining hard-success and diversity margins.',
        'position': 'Compare continuous u/v proposal spread with selected and final all-pair positional spread across four repeats; identify contraction without assuming a baseline retention ratio.',
        'valid_position': 'Compare u/v spread only among hard-valid seed pairs, together with valid-pair counts; distinguish selection contraction from validity filtering.',
        'rotation': 'Compare yaw spread at initialization, proposals, projected incumbents and final outputs across four repeats; test whether projection or selection concentrates orientation.',
        'preference': 'Compare continuous u/v/yaw preference residuals on feasible outputs and identify a bounded local improvement that preserves seed-dependent spread.',
        None: 'Establish current hard-success, runtime and preference measurements and paired diversity baselines before asserting a violated comparative objective.',
    }
    diagnosis = {'objective': objective, 'status': status, 'evidence': evidence[:12],
                 'probe': experiment, 'expected_observation': expectations[objective][:600]}
    analysis = ('Current diagnosis: ' + str(objective) + ' (' + status + '). ' +
                expectations[objective] + ' Evidence: ' + ', '.join(evidence) +
                '. Historical failed improvements do not establish a current violation. '
                'Without a measured candidate comparison, retention and improvement margins are unknown. '
                'Single-layout optimizer_loss does not constrain cross-seed diversity.')[:3000]
    if not proposing:
        return {'experiment': experiment, 'analysis': analysis, 'diagnosis': diagnosis}
    indices = _condition_groups(packet, objective)
    prompt = '''Edit the supplied INITIAL GENERATOR in place using the trusted contract and actual source. Make one coherent, bounded algorithm-level improvement. Preserve its API, allowed imports, continuous Mesh physics, assets, camera constraints, random-input semantics and all execution/query limits. The upstream meta-policy and controller are fixed.

Inspect the selected complete four-repeat condition groups, including the chosen probe. Trace continuous u/v/yaw initialization, proposal generation, Mesh queries, projection, incumbent updates, final selection and fallbacks. Compare outcomes within each condition across all four seeds; pair_metrics summarizes six seed pairs. Packet room geometry is context, not evidence of generated positions. Historical acceptance rejection does not imply physical failure. Query-only selector replays do not validate final physics or adaptive incumbent history.

Use the current diagnosis below to locate the earliest demonstrated mechanism. Separate hard failure, visibility, all-pair positional spread, valid-pair spread and counts, yaw diversity, preference and runtime. If only a baseline exists, comparative retention and preference improvement remain unknown; do not claim collapse from an absolute pair metric. Historical ablations may suggest hypotheses but cannot replace current evidence. Inspect existing safeguards before replacing any construction.

Choose one bounded repair with an explicit invariant in the existing continuous u/v/yaw search. For diversity, distinguish contracted proposals from contracted final selection before changing either; a scalar per-layout optimizer_loss supplies no cross-seed guarantee. Preserve useful seed-dependent translation and yaw while improving preference within feasible outputs. Verify initialization, updates, endpoint selection and every fallback against the same invariant. Avoid discrete board/deadlock assumptions unless the actual source and physics establish them. Do not increase query counts or execution budgets.

Frozen joint acceptance requires zero runtime errors, no hard-success count loss, visibility loss <=0.01, ALL-pair and VALID-pair positional retention >=90%, rotation retention >=80%, no valid-pair count loss and preference improvement >=0.01m. Neither policy nor generator may alter thresholds, oracle, tasks or resources. Treat these as separate measured objectives; a lower optimizer_loss alone is insufficient. Explain the expected mechanism and unresolved margins, with no claims of unseen gains.
'''
    prompt += '\nCurrent diagnosis: ' + analysis + '\nResearch observation: ' + expectations[objective]
    if not indices:
        analysis = (analysis + ' No complete four-repeat probe group was supplied; the required evidence selection cannot be formed.')[:3000]
    return {'analysis': analysis, 'prompt': prompt[:8000],
            'example_indices': indices, 'diagnosis': diagnosis}


def _legacy_layout(packet):
    summary = packet.get('summary', {})
    if packet.get('phase') == 'choose':
        return _choose(summary)
    examples = packet.get('examples', [])
    if not isinstance(examples, list):
        examples = []
    examples = [row if isinstance(row, dict) else {} for row in examples]
    objective, risks, objective_notes = _diagnose(summary, examples)
    indices = _select(examples, objective)
    counts = {}
    for row in examples:
        name = _features(row)[0]
        counts[name] = counts.get(name, 0) + 1
    observed = '; '.join(str(k) + '=' + str(v) for k, v in sorted(counts.items()))[:650]
    evidence = _brief(summary)
    analysis = ('Prioritize the earliest demonstrated bottleneck: contract validity, first-output solvability, then useful difficulty and diversity within runtime. Summary: ' + evidence + '. Example outcomes: ' + observed + '. Selected examples cover distinct error classes, successful difficulty bands, dimensions and input density, with matched success/failure strata where available. Input geometry is context, not evidence of generated object positions. Small dev samples support hypotheses, not generalization claims.')[:3000]
    guidance = ''
    if objective is not None:
        guidance = ('Acceptance diagnosis: prioritize ' + objective + '. Measured deficits: ' + objective_notes +
                    '. Historical valid/solvable flags describe runnable code and joint acceptance, not scene physics. '
                    'Compare baseline/candidate hard success, runtime, visibility, ALL-pair and VALID-pair position spread, rotation spread, valid-pair count and preference separately. '
                    'Preserve no hard-success loss, zero runtime errors, visibility loss <=0.01, position retention >=90% for both pair populations, rotation retention >=80%, no valid-pair count loss, and preference improvement >=0.01m. '
                    'Do not infer retention from an absolute pair metric without its baseline. Query-only selector replays are mechanism clues, not validated replacements. '
                    'For diversity deficits, trace both proposal spread and final selection concentration before choosing the repair; improving preference alone can fail joint acceptance. '
                    'Use repeated-condition examples to inspect seed-dependent position and orientation variation. Do not increase query or execution budgets. ')
        analysis = (guidance + ' Summary: ' + evidence + '. Example outcomes: ' + observed)[:3000]
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
    if guidance:
        # Reserve space for current evidence even when the strategy fills its budget.
        tail = ('\nObserved summary: ' + evidence + '\nExample outcome counts: ' + observed +
                '\nSelected example indices (zero-based): ' + str(indices))
        prompt = guidance + '\n\n' + prompt[:max(0, 8000 - len(guidance) - len(tail) - 2)] + tail
    return {'analysis': analysis, 'example_indices': indices, 'prompt': prompt[:8000]}
