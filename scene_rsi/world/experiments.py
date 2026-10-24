"""Fixed agents, independent solvability checks and structured generator trials."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import re
import resource
import time

from .task import GeneratorConfig, generate
from .planning import solve, fixed_plan
from .engine import World
from ..benchmark.runner import git_provenance


def evaluate(task, max_steps=80):
    started, cpu = time.perf_counter(), time.process_time()
    reference = solve(task)
    actions = fixed_plan(task, max_steps=max_steps)
    with World(task) as world:
        trajectory = [world.snapshot()]
        for action in actions:
            trajectory.append(world.step(action))
            if world.success or trajectory[-1]["event"] == "physics_collision":
                break
        success, snapshot = world.success, world.snapshot()
    reference_replayed = None
    if reference["status"] == "solvable":
        with World(task) as oracle_world:
            for action in reference["actions"]:
                oracle_world.step(action)
            reference_replayed = oracle_world.success and oracle_world.collisions == 0
    timeout = not success and snapshot["steps"] >= max_steps
    if reference_replayed is False:
        outcome = "physics_mismatch"
    elif success:
        outcome = "success"
    elif reference["status"] == "unsolvable":
        outcome = "scene_unsolvable"
    elif reference["status"] == "unknown":
        outcome = "solvability_unknown"
    else:
        outcome = "agent_timeout" if timeout else "agent_insufficient"
    return {"scene_id": task.spec.scene_id, "outcome": outcome, "success": success,
            "solvability": reference["status"], "reference_replayed": reference_replayed,
            "timeout": timeout, "steps": snapshot["steps"], "pushes": snapshot["pushes"],
            "collisions": snapshot["collisions"], "trajectory": trajectory,
            "reference_actions": reference["actions"], "search_states": reference["explored"],
            "wall_seconds": time.perf_counter()-started, "cpu_seconds": time.process_time()-cpu,
            "timing_scope": "solvability search + fixed agent + reference replay, excluding generation and rendering",
            "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
            "rss_scope": "controller process lifetime high-water mark", "gpu_used": False}


def summarize(rows):
    count = len(rows)
    kinds = sorted({r["outcome"] for r in rows})
    solvable = [r for r in rows if r["solvability"] == "solvable" and r["reference_replayed"]]
    return {"tasks": count, "success_rate": sum(r["success"] for r in rows)/count,
            "solvable_rate": len(solvable)/count,
            "success_given_solvable": sum(r["success"] for r in solvable)/len(solvable) if solvable else None,
            "outcomes": {kind: sum(r["outcome"] == kind for r in rows) for kind in kinds},
            "collisions": sum(r["collisions"] for r in rows), "timeouts": sum(r["timeout"] for r in rows),
            "wall_seconds": sum(r["wall_seconds"] for r in rows),
            "cpu_seconds": sum(r["cpu_seconds"] for r in rows),
            "generation_wall_seconds": sum(r.get("generation_wall_seconds", 0) for r in rows),
            "generation_cpu_seconds": sum(r.get("generation_cpu_seconds", 0) for r in rows),
            "reference_steps_mean": sum(len(r["reference_actions"]) for r in solvable)/len(solvable) if solvable else None}


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def evaluate_seeds(config, seeds, out):
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    for seed in seeds:
        started, cpu = time.perf_counter(), time.process_time()
        task = generate(seed, config)
        generation_wall, generation_cpu = time.perf_counter()-started, time.process_time()-cpu
        result = evaluate(task)
        result.update(generation_wall_seconds=generation_wall, generation_cpu_seconds=generation_cpu)
        result["seed"] = seed
        save(out/f"scene_{seed}.json", task.to_dict())
        save(out/f"result_{seed}.json", result)
        rows.append(result)
    summary = summarize(rows)
    save(out/'summary.json', summary)
    return summary


def experiment(out, proposals, dev_seeds=range(100, 112), val_seeds=range(1000, 1008)):
    """Only two generator booleans can change; no code, tools, imports or paths."""
    if type(proposals) is not list or len(proposals) > 4:
        raise ValueError("supply at most four structured generator proposals")
    candidates = [("baseline", GeneratorConfig())]
    for proposal in proposals:
        if type(proposal) is not dict or set(proposal) != {"name", "changes"}:
            raise ValueError("proposal requires exactly name and changes")
        name, changes = proposal["name"], proposal["changes"]
        if type(name) is not str or not re.fullmatch(r"[a-z0-9_-]{1,40}", name) or name in {c[0] for c in candidates}:
            raise ValueError("proposal names must be unique simple identifiers")
        if type(changes) is not dict or not changes or set(changes)-{"avoid_corners", "require_solvable"}:
            raise ValueError("only avoid_corners and require_solvable can change")
        config = replace(GeneratorConfig(), **changes)
        config.validate()
        candidates.append((name, config))
    dev_seeds, val_seeds = list(dev_seeds), list(val_seeds)
    if not dev_seeds or not val_seeds or set(dev_seeds) & set(val_seeds):
        raise ValueError("dev and val seeds must be nonempty and disjoint")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    trials = []
    for name, config in candidates:
        trial = {"name": name, "config": asdict(config),
                 "dev": evaluate_seeds(config, dev_seeds, out/name/'dev')}
        trials.append(trial)
        # This aggregate is the only feedback intended for an improvement agent.
        save(out/'dev_feedback.json', {"allowed_changes": ["avoid_corners", "require_solvable"],
                                      "trials": trials, "instruction": "Return name and boolean changes only; no Python."})
    best = max(trials, key=lambda row: (row["dev"]["success_rate"], row["dev"]["solvable_rate"]))
    configs = dict(candidates)
    validation = {name: evaluate_seeds(configs[name], val_seeds, out/name/'val')
                  for name in dict.fromkeys(("baseline", best["name"]))}
    baseline_val, best_val = validation["baseline"], validation[best["name"]]
    accepted = (best_val["success_rate"] >= baseline_val["success_rate"]
                and best_val["solvable_rate"] >= baseline_val["solvable_rate"])
    selected = best["name"] if accepted else "baseline"
    save(out/'selected_generator.json', asdict(configs[selected]))
    report = {"trials": trials, "selected": selected, "validation": validation,
              "git": git_provenance(),
              "selection": "dev task success then solvability; val requires both rates to not regress",
              "dev_seeds": dev_seeds, "val_seeds": val_seeds,
              "comparison": "same seeds and geometry budget; generated layouts differ between generators",
              "agent_changed": False, "arbitrary_candidate_code_executed": False,
              "model_calls": 0, "is_rsi": False}
    save(out/'summary.json', report)
    return report
