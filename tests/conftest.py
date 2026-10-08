import json

import pytest

from registry.demo import seed
from registry.sandbox import DockerSandbox, docker_available
from registry.service import SkillRegistry
from registry.store import MemoryStore


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def registry(store):
    return SkillRegistry(store)


@pytest.fixture
def ids(registry):
    return seed(registry)


@pytest.fixture(scope="session")
def sandbox():
    if not docker_available():
        pytest.skip("Docker is not available")
    return DockerSandbox()


def make_skill(tmp_path, name, description, body="Do the thing.", files=None, triggers=None):
    """Write a skill folder and return its path."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n{body}\n")
    for rel, text in (files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    if triggers is not None:
        (root / "evals").mkdir(exist_ok=True)
        (root / "evals" / "triggers.jsonl").write_text(
            "\n".join(json.dumps({"query": q, "should_trigger": s}) for q, s in triggers) + "\n")
    return root
