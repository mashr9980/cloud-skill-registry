"""The Claude tool-use loop, driven by a scripted stand-in for the API client.
No network and no API key: this checks the loop's message shapes and tool wiring."""
import json
from types import SimpleNamespace as NS

from agent.loop import MAX_LISTED, run_agent, system_prompt
from agent.tools import TOOL_DEFS, SkillTools
from registry.demo import ACME_SUPPORT
from registry.service import SkillCard


def tool_use(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class ScriptedClient:
    def __init__(self, turns):
        self.turns, self.requests = list(turns), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        stop, content = self.turns.pop(0)
        return NS(stop_reason=stop, content=content)


def test_loop_loads_skill_reads_file_and_answers(registry, ids):
    skill = ids["platform/invoice-totals"]
    client = ScriptedClient([
        ("tool_use", [tool_use("t1", "load_skill", skill_id=skill)]),
        ("tool_use", [tool_use("t2", "read_skill_file", skill_id=skill, path="references/rounding.md"),
                      tool_use("t3", "load_skill", skill_id="skill_doesnotexist")]),
        ("end_turn", [NS(type="text", text="Rounded half-up at the end.")]),
    ])
    tools = SkillTools(registry, registry.open_session(ACME_SUPPORT))
    run = run_agent(client, tools, "why is my invoice off by a cent?")

    assert run.final_text == "Rounded half-up at the end." and run.stop_reason == "end_turn"
    first = client.requests[0]
    assert first["model"] == "claude-opus-5-5" and first["tools"] == TOOL_DEFS
    assert skill in first["system"] and "scripts/total.py" not in first["system"]  # level 1 only

    # Both results from the parallel turn go back in ONE user message, errors flagged.
    results = run.messages[4]["content"]
    assert [r["tool_use_id"] for r in results] == ["t2", "t3"]
    assert "half-up" in json.loads(results[0]["content"])["content"] and results[0]["is_error"] is False
    assert results[1]["is_error"] is True


def test_stop_after_first_load_for_trigger_evals(registry, ids):
    skill = ids["platform/csv-profile"]
    client = ScriptedClient([("tool_use", [tool_use("t1", "load_skill", skill_id=skill)])])
    tools = SkillTools(registry, registry.open_session(ACME_SUPPORT))
    run = run_agent(client, tools, "profile this csv", stop_after_load=True)
    assert [c["input"]["skill_id"] for c in run.tool_calls if c["tool"] == "load_skill"] == [skill]
    assert len(client.requests) == 1


def test_large_catalogs_switch_to_search():
    cards = [SkillCard(f"s{i}", f"skill-{i}", "x", 1) for i in range(MAX_LISTED + 1)]
    prompt = system_prompt(cards)
    assert "search_skills" in prompt and "skill-0" not in prompt


def test_tool_schemas_are_strict():
    for tool in TOOL_DEFS:
        schema = tool["input_schema"]
        assert tool["strict"] and schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
