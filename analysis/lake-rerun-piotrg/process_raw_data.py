#!/usr/bin/env python3
"""Extract comparable per-trial metrics from the final Lake reruns."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Iterable


GPT_PRICES = {
    "input": 5.00,
    "cache_read": 0.50,
    "output": 30.00,
    "long_input": 10.00,
    "long_cache_read": 1.00,
    "long_output": 45.00,
}
GPT_LONG_CONTEXT_THRESHOLD = 272_000
FABLE_PRICES = {
    "input": 10.00,
    "cache_read": 1.00,
    "cache_write": 12.50,
    "output": 50.00,
}

TASK_NAMES = {
    "stage-01-level-01": "01 Icy Waters",
    "stage-01-level-02": "02 Turns",
    "stage-01-level-03": "03 Affection",
    "stage-01-level-04": "04 Pillar Yard",
    "stage-01-level-05": "05 Brick Wall",
    "stage-01-level-06": "06 Lock",
    "stage-01-level-07": "07 Novice Locksmith",
    "stage-01-level-08": "08 Locked In",
    "stage-01-level-10": "10 Two Doors",
    "stage-01-level-11": "11 Jelly Throne",
    "stage-01-level-12": "12 Crab Storage",
    "stage-01-level-13": "13 Burglary",
    "stage-01-bonus-00": "b1 Submerged Ruins",
    "stage-01-bonus-01": "b2 Sunken Temple",
}

FIELDS = [
    "run",
    "model",
    "harness",
    "task",
    "status",
    "reward",
    "agent_sec",
    "trial_sec",
    "uncached_input",
    "cache_read",
    "cache_write",
    "output",
    "reasoning_output",
    "total_tokens",
    "api_calls",
    "cost_usd",
    "cost_source",
    "notes",
]


def money(value: Decimal | float | str) -> str:
    # Half-token price increments make seven decimal places meaningful here.
    return str(Decimal(str(value)).quantize(Decimal("0.0000001"), rounding=ROUND_HALF_EVEN))


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def duration_seconds(start: str, finish: str) -> float:
    return (parse_time(finish) - parse_time(start)).total_seconds()


def iter_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON in {path}:{line_number}") from error


def common_fields(result: dict[str, Any]) -> dict[str, Any]:
    task_id = result["task_name"]
    if task_id not in TASK_NAMES:
        raise ValueError(f"Unexpected task in Lake rerun: {task_id}")

    reward = (
        (result.get("verifier_result") or {}).get("rewards") or {}
    ).get("reward")
    if reward == 1:
        status = "success"
    elif reward == 0:
        status = "failure"
    else:
        status = "stopped/no reward"

    agent = result["agent_execution"]
    return {
        "task": TASK_NAMES[task_id],
        "status": status,
        "reward": "" if reward is None else reward,
        "agent_sec": round(duration_seconds(agent["started_at"], agent["finished_at"]), 1),
        "trial_sec": round(duration_seconds(result["started_at"], result["finished_at"]), 1),
    }


def find_one(root: Path, pattern: str) -> Path:
    matches = list(root.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {pattern!r} below {root}; found {len(matches)}")
    return matches[0]


def gpt_usage(trial: Path) -> dict[str, Any]:
    rollout = find_one(trial, "agent/sessions/**/rollout-*.jsonl")
    previous = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                "reasoning_output_tokens": 0, "total_tokens": 0}
    seen: set[tuple[int, ...]] = set()
    cost = Decimal(0)
    calls = 0

    for event in iter_json_lines(rollout):
        if event.get("type") != "event_msg" or event.get("payload", {}).get("type") != "token_count":
            continue
        info = event["payload"].get("info")
        if not info:
            continue
        total = info["total_token_usage"]
        signature = tuple(total.get(key, 0) for key in previous)
        if signature in seen:
            continue
        seen.add(signature)
        delta = {key: total.get(key, 0) - previous[key] for key in previous}
        if any(value < 0 for value in delta.values()):
            raise ValueError(f"Non-monotonic Codex usage in {rollout}")

        prompt = delta["input_tokens"]
        cached = delta["cached_input_tokens"]
        uncached = prompt - cached
        if uncached < 0:
            raise ValueError(f"Cached input exceeds total input in {rollout}")
        long_context = prompt >= GPT_LONG_CONTEXT_THRESHOLD
        input_price = GPT_PRICES["long_input" if long_context else "input"]
        cache_price = GPT_PRICES["long_cache_read" if long_context else "cache_read"]
        output_price = GPT_PRICES["long_output" if long_context else "output"]
        cost += (
            uncached * Decimal(str(input_price))
            + cached * Decimal(str(cache_price))
            + delta["output_tokens"] * Decimal(str(output_price))
        ) / Decimal(1_000_000)
        previous = {key: total.get(key, 0) for key in previous}
        calls += 1

    if not calls:
        raise ValueError(f"No Codex token-count events in {rollout}")

    return {
        "uncached_input": previous["input_tokens"] - previous["cached_input_tokens"],
        "cache_read": previous["cached_input_tokens"],
        "cache_write": 0,
        "output": previous["output_tokens"],
        "reasoning_output": previous["reasoning_output_tokens"],
        "total_tokens": previous["total_tokens"],
        "api_calls": calls,
        "cost_usd": money(cost),
        "cost_source": "computed from de-duplicated per-call usage",
        "notes": "Codex cumulative token_count events; cached input is a subset of input",
    }


def fable_trajectory_usage(trial: Path) -> dict[str, int]:
    trajectory = json.loads((trial / "agent/trajectory.json").read_text())
    previous_signature: tuple[int, ...] | None = None
    totals = {"uncached_input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "api_calls": 0}

    for step in trajectory["steps"]:
        metrics = step.get("metrics")
        if not metrics:
            continue
        extra = metrics.get("extra") or {}
        signature = (
            metrics.get("prompt_tokens", 0),
            metrics.get("completion_tokens", 0),
            metrics.get("cached_tokens", 0),
            extra.get("cache_creation_input_tokens", 0),
            extra.get("cache_read_input_tokens", 0),
        )
        if signature == previous_signature:
            continue
        previous_signature = signature
        prompt, output, cached, cache_write, cache_read = signature
        if cache_read != cached:
            raise ValueError(f"Conflicting Claude cache-read fields in {trial}")
        uncached = prompt - cached
        if uncached < 0:
            raise ValueError(f"Cached input exceeds prompt input in {trial}")
        totals["uncached_input"] += uncached
        totals["cache_read"] += cache_read
        totals["cache_write"] += cache_write
        totals["output"] += output
        totals["api_calls"] += 1
    return totals


def fable_usage(trial: Path) -> dict[str, Any]:
    derived = fable_trajectory_usage(trial)
    final_result = None
    for event in iter_json_lines(trial / "agent/claude-code.txt"):
        if event.get("type") == "result":
            final_result = event

    if final_result:
        usage = final_result["usage"]
        authoritative = {
            "uncached_input": usage.get("input_tokens", 0),
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "cache_write": usage.get("cache_creation_input_tokens", 0),
            "output": usage.get("output_tokens", 0),
        }
        for key, value in authoritative.items():
            if derived[key] != value:
                raise ValueError(
                    f"De-duplicated trajectory {key}={derived[key]} does not match "
                    f"Claude result {value} in {trial}"
                )
        cost = final_result["total_cost_usd"]
        cost_source = "Claude Code result.total_cost_usd"
        notes = "Final Claude Code result usage; trajectory used only to count API responses"
    else:
        authoritative = {key: derived[key] for key in ("uncached_input", "cache_read", "cache_write", "output")}
        cost = (
            authoritative["uncached_input"] * Decimal(str(FABLE_PRICES["input"]))
            + authoritative["cache_read"] * Decimal(str(FABLE_PRICES["cache_read"]))
            + authoritative["cache_write"] * Decimal(str(FABLE_PRICES["cache_write"]))
            + authoritative["output"] * Decimal(str(FABLE_PRICES["output"]))
        ) / Decimal(1_000_000)
        cost_source = "computed from de-duplicated trajectory"
        notes = "Lower bound: stopped during an API call, so an unrecorded in-flight response may exist"

    return {
        **authoritative,
        "reasoning_output": "included-in-output",
        "total_tokens": sum(authoritative.values()),
        "api_calls": derived["api_calls"],
        "cost_usd": money(cost),
        "cost_source": cost_source,
        "notes": notes,
    }


def trials(job: Path) -> list[tuple[Path, dict[str, Any]]]:
    found = []
    for result_path in job.glob("*/result.json"):
        found.append((result_path.parent, json.loads(result_path.read_text())))
    if len(found) != len(TASK_NAMES):
        raise ValueError(f"Expected {len(TASK_NAMES)} trials in {job}; found {len(found)}")
    task_ids = {result["task_name"] for _, result in found}
    if task_ids != set(TASK_NAMES):
        raise ValueError(f"Lake task mismatch in {job}: {sorted(task_ids ^ set(TASK_NAMES))}")
    return sorted(found, key=lambda pair: TASK_NAMES[pair[1]["task_name"]])


def process_job(job: Path, run: str, model: str, harness: str, usage_parser: Any) -> list[dict[str, Any]]:
    rows = []
    for trial, result in trials(job):
        rows.append({
            "run": run,
            "model": model,
            "harness": harness,
            **common_fields(result),
            **usage_parser(trial),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpt-job", type=Path, required=True)
    parser.add_argument("--fable-job", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("processed_data.csv"))
    args = parser.parse_args()

    rows = process_job(args.gpt_job, "GPT-5.6 Sol high / Codex", "GPT-5.6 Sol", "Codex", gpt_usage)
    rows += process_job(args.fable_job, "Claude Fable 5 high / Claude Code", "Claude Fable 5", "Claude Code", fable_usage)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
