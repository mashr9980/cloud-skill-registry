"""A manual Claude tool-use loop over the skill tools.

Level 1 (names + descriptions) goes in the system prompt. Past `MAX_LISTED`
skills even that list costs too much context, so the prompt lists none and the
agent searches instead, the same move as Anthropic's Tool Search Tool.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from registry.service import SkillCard

from .tools import TOOL_DEFS, SkillTools

MODEL = "claude-opus-5-5"
MAX_LISTED = 30

SYSTEM = """You are an agent on a workflow platform. Skills are packaged instructions and scripts kept in a central registry. Their full instructions are not in this prompt.

{skills_block}

When a task matches a skill, call load_skill with its id, then follow those instructions. Read a skill file or run a skill script only when the instructions call for it. If nothing fits, do the task without a skill and say so."""


def system_prompt(cards: list[SkillCard]) -> str:
    if not cards:
        block = "No skills are available in this session."
    elif len(cards) <= MAX_LISTED:
        lines = "\n".join(f"- {c.skill_id} | {c.name}: {c.description}" for c in cards)
        block = f"Skills available in this session (id | name: description):\n{lines}"
    else:
        block = (f"This session has {len(cards)} skills. Call search_skills with what the task needs "
                 "to find the right one.")
    return SYSTEM.format(skills_block=block)


@dataclass
class AgentRun:
    final_text: str
    stop_reason: str
    tool_calls: list[dict]
    messages: list[dict] = field(repr=False, default_factory=list)


def run_agent(client: Any, tools: SkillTools, user_message: str, max_turns: int = 12,
              effort: str = "medium", stop_after_load: bool = False) -> AgentRun:
    """`client` is an anthropic.Anthropic(). `stop_after_load` ends the run at the first
    load_skill call; the live trigger eval uses it to measure which skill fires."""
    system = system_prompt(tools.registry.cards(tools.session_id))
    messages: list[dict] = [{"role": "user", "content": user_message}]
    response = None
    for _ in range(max_turns):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system,
            tools=TOOL_DEFS,
            messages=messages,
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},  # system + tools are stable for the whole session
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # on a policy decline, the API re-runs on a fallback model
        )
        # Append the content unchanged: thinking blocks must be passed back as they came.
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason != "tool_use":
            break  # end_turn, refusal, max_tokens
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            content, is_error = tools.execute(block.name, block.input)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": content, "is_error": is_error})
        messages.append({"role": "user", "content": results})  # all results in one message
        if stop_after_load and any(c["tool"] == "load_skill" for c in tools.calls):
            break
    text = "".join(b.text for b in (response.content if response else []) if b.type == "text")
    return AgentRun(text, response.stop_reason if response else "no_response", tools.calls, messages)
