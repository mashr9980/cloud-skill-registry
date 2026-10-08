"""Trigger eval: does the right skill fire, and stay quiet on near-misses?

Every bundle ships `evals/triggers.jsonl`, one case per line:
    {"query": "...", "should_trigger": true}
A candidate version is scored against everything else the publisher can see, so
a new description that steals traffic from a neighbouring skill fails here, not
in production. The method follows agentskills.io's guidance on description
evals: a mix of should-trigger prompts and near-miss should-not prompts.

This measures the retrieval step offline and for free. `evals/run_trigger_eval.py
--live` runs the same cases through the Claude agent loop instead.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .search import Doc, HybridIndex, route

MIN_CASES = 6
MIN_EACH_SIDE = 2
PASS_RATE = 0.85


@dataclass(frozen=True)
class CaseResult:
    query: str
    should_trigger: bool
    fired: str | None
    passed: bool


def load_cases(files: dict[str, bytes]) -> list[dict]:
    raw = files.get("evals/triggers.jsonl", b"").decode("utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def trigger_eval(skill_id: str, neighbours: list[Doc], candidate: Doc, cases: list[dict]) -> dict:
    docs = [d for d in neighbours if d.skill_id != skill_id] + [candidate]
    index = HybridIndex(docs)
    results = []
    for case in cases:
        hit = route(index, case["query"])
        fired = hit.name if hit else None
        did_fire = hit is not None and hit.skill_id == skill_id
        results.append(CaseResult(case["query"], bool(case["should_trigger"]), fired,
                                  did_fire == bool(case["should_trigger"])))
    positives = sum(r.should_trigger for r in results)
    negatives = len(results) - positives
    rate = sum(r.passed for r in results) / len(results) if results else 0.0
    enough = len(results) >= MIN_CASES and positives >= MIN_EACH_SIDE and negatives >= MIN_EACH_SIDE
    passed = enough and rate >= PASS_RATE
    summary = (f"{sum(r.passed for r in results)}/{len(results)} cases passed "
               f"({rate:.0%}; {positives} should-fire, {negatives} near-miss)")
    if not enough:
        summary += f"; needs at least {MIN_CASES} cases with {MIN_EACH_SIDE}+ on each side"
    return {"passed": passed, "pass_rate": rate, "summary": summary,
            "cases": [asdict(r) for r in results]}
