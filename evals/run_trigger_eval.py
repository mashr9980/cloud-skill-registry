"""Trigger accuracy for every example skill, per principal.

    python -m evals.run_trigger_eval           # offline: the hybrid retrieval router, free
    python -m evals.run_trigger_eval --live    # Claude decides; counts which skill it loads first

--live needs Anthropic credentials and makes one or more API calls per case.
Each case runs `--runs` times (default 3) and passes on a majority, because
model choices vary run to run.
"""
from __future__ import annotations

import argparse
import sys

from registry.bundle import pack_dir, read_bundle
from registry.demo import ACME_SALES, ACME_SUPPORT, EXAMPLES, GLOBEX_SUPPORT, LAYOUT, seed
from registry.evals import load_cases
from registry.service import SkillRegistry
from registry.store import MemoryStore

VIEWERS = {"acme": ACME_SUPPORT, "globex": GLOBEX_SUPPORT, "platform": ACME_SUPPORT}


def cases_by_skill(ids: dict[str, str]):
    for who, _scope, rel in LAYOUT:
        name = rel.rsplit("/", 1)[-1]
        viewer = ACME_SALES if name == "crm-lookup" else VIEWERS[who.tenant]
        yield ids[f"{who.tenant}/{name}"], name, who, viewer, load_cases(read_bundle(pack_dir(EXAMPLES / rel)))


def offline(registry: SkillRegistry, ids: dict[str, str]) -> int:
    failures = 0
    for skill_id, name, owner, _viewer, _cases in cases_by_skill(ids):
        report = registry.evaluate(owner, skill_id, 1)
        print(f"{name:16} {report['summary']}")
        for case in report["cases"]:
            if not case["passed"]:
                failures += 1
                want = "fire" if case["should_trigger"] else "stay quiet"
                print(f"    MISS  wanted it to {want}: {case['query']!r} -> fired {case['fired']}")
    return failures


def live(registry: SkillRegistry, ids: dict[str, str], runs: int) -> int:
    import anthropic

    from agent.loop import run_agent
    from agent.tools import SkillTools

    client = anthropic.Anthropic()
    failures = 0
    for skill_id, name, _owner, viewer, cases in cases_by_skill(ids):
        passed = 0
        for case in cases:
            fired_count = 0
            for _ in range(runs):
                tools = SkillTools(registry, registry.open_session(viewer))
                run = run_agent(client, tools, case["query"], max_turns=3, effort="low", stop_after_load=True)
                loaded = [c["input"]["skill_id"] for c in run.tool_calls if c["tool"] == "load_skill"]
                fired_count += bool(loaded) and loaded[0] == skill_id
            ok = (fired_count / runs > 0.5) == bool(case["should_trigger"])
            passed += ok
            if not ok:
                failures += 1
                print(f"    MISS  {case['query']!r}: fired {fired_count}/{runs} runs")
        print(f"{name:16} {passed}/{len(cases)} cases passed (live, {runs} runs each)")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    registry = SkillRegistry(MemoryStore())
    ids = seed(registry)
    failures = live(registry, ids, args.runs) if args.live else offline(registry, ids)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
