"""The four tools an agent gets. Each maps to one level of progressive disclosure:

    search_skills     -> level 1 (names + descriptions, ranked)
    load_skill        -> level 2 (SKILL.md body of the pinned version)
    read_skill_file   -> level 3 (one reference/asset file)
    run_skill_script  -> level 3 (a bundled script, in the sandbox)

The same four calls could be exposed as MCP tools unchanged; nothing here reads
the local disk.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from registry.bundle import BundleError
from registry.sandbox import DockerSandbox, SandboxError
from registry.secrets import SecretVault
from registry.service import NotFound, SkillRegistry


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOL_DEFS = [
    {
        "name": "search_skills",
        "description": "Search the skills available in this session by what the task needs. "
                       "Returns up to 5 skills with ids, names and descriptions. Use it when no "
                       "skill listed in the system prompt clearly fits.",
        "input_schema": _schema({"query": {"type": "string"}}, ["query"]),
        "strict": True,
    },
    {
        "name": "load_skill",
        "description": "Load a skill's full instructions and its file list. Call this before "
                       "following a skill; the system prompt only has its one-line description.",
        "input_schema": _schema({"skill_id": {"type": "string"}}, ["skill_id"]),
        "strict": True,
    },
    {
        "name": "read_skill_file",
        "description": "Read one file listed by load_skill, such as references/*.md. Only read a "
                       "file when the skill's instructions point to it.",
        "input_schema": _schema({"skill_id": {"type": "string"}, "path": {"type": "string"}},
                                ["skill_id", "path"]),
        "strict": True,
    },
    {
        "name": "run_skill_script",
        "description": "Run a script from a loaded skill (path starts with scripts/) in an isolated "
                       "sandbox with no network. Pass input data on stdin. Returns exit code, "
                       "stdout and stderr.",
        "input_schema": _schema({
            "skill_id": {"type": "string"},
            "script": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
            "stdin": {"type": "string"},
        }, ["skill_id", "script", "args", "stdin"]),
        "strict": True,
    },
]


@dataclass
class SkillTools:
    registry: SkillRegistry
    session_id: str
    sandbox: DockerSandbox | None = None
    vault: SecretVault = field(default_factory=SecretVault)
    calls: list[dict] = field(default_factory=list)

    def execute(self, name: str, args: dict) -> tuple[str, bool]:
        """Returns (content, is_error). Errors go back to the model as tool results, not exceptions."""
        self.calls.append({"tool": name, "input": args})
        try:
            return json.dumps(self._dispatch(name, args)), False
        except (NotFound, SandboxError, BundleError, KeyError, ValueError) as err:
            return json.dumps({"error": str(err)}), True

    def _dispatch(self, name: str, a: dict) -> dict:
        if name == "search_skills":
            hits = self.registry.search(self.session_id, a["query"])
            cards = {c.skill_id: c for c in self.registry.cards(self.session_id)}
            return {"skills": [{"skill_id": h.skill_id, "name": h.name,
                                "description": cards[h.skill_id].description} for h in hits]}
        if name == "load_skill":
            return self.registry.load(self.session_id, a["skill_id"])
        if name == "read_skill_file":
            return {"path": a["path"], "content": self.registry.read_file(self.session_id, a["skill_id"], a["path"])}
        if name == "run_skill_script":
            if self.sandbox is None:
                raise SandboxError("no sandbox configured for this session")
            files, metadata, version = self.registry.bundle_files(self.session_id, a["skill_id"])
            tenant = self.registry.principal(self.session_id).tenant
            result = self.sandbox.run(files, metadata, a["script"], a.get("args", []), a.get("stdin", ""),
                                      secrets=self.vault.for_skill(tenant, metadata))
            return {"version": version, **result.__dict__}
        raise KeyError(f"unknown tool {name}")
