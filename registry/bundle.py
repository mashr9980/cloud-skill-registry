"""Skill bundles: parse SKILL.md, pack a skill folder, read a bundle safely.

A bundle is a deterministic tar.gz of one skill folder. Its sha256 is the
storage key, so the same bytes always land under the same key and a stored
version can never change without its hash changing too.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import posixpath
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # Agent Skills spec: lowercase, single hyphens
MAX_FILES = 200
MAX_BUNDLE_BYTES = 5 * 1024 * 1024


class BundleError(ValueError):
    pass


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    body: str
    metadata: dict[str, str] = field(default_factory=dict)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_skill_md(text: str) -> SkillManifest:
    """Validate frontmatter against the Agent Skills spec (agentskills.io/specification)."""
    if not text.startswith("---"):
        raise BundleError("SKILL.md must start with YAML frontmatter")
    try:
        _, front, body = text.split("---", 2)
    except ValueError:
        raise BundleError("SKILL.md frontmatter is not closed with ---") from None
    data = yaml.safe_load(front) or {}
    if not isinstance(data, dict):
        raise BundleError("frontmatter must be a mapping")

    name = str(data.get("name", ""))
    if not (1 <= len(name) <= 64) or not NAME_RE.match(name):
        raise BundleError(f"invalid skill name {name!r}: 1-64 chars, a-z 0-9 and single hyphens")
    description = str(data.get("description", "")).strip()
    if not (1 <= len(description) <= 1024):
        raise BundleError("description must be 1-1024 characters")
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict) or not all(isinstance(v, str) for v in metadata.values()):
        raise BundleError("metadata must map strings to strings")
    return SkillManifest(name=name, description=description, body=body.strip(), metadata=metadata)


def pack_dir(path: str | Path) -> bytes:
    """Deterministic tar.gz: sorted entries, zeroed times and owners."""
    root = Path(path)
    if not (root / "SKILL.md").is_file():
        raise BundleError(f"{root} has no SKILL.md")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for file in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = file.relative_to(root).as_posix()
            data = file.read_bytes()
            info = tarfile.TarInfo(rel)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            info.uid = info.gid = 0
            tar.addfile(info, io.BytesIO(data))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def read_bundle(data: bytes) -> dict[str, bytes]:
    """Unpack into memory. Rejects links, absolute paths, '..', and oversize bundles."""
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError("bundle too large")
    files: dict[str, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise BundleError(f"{member.name}: only regular files are allowed")
            name = posixpath.normpath(member.name)
            if name.startswith(("/", "..")) or "/../" in f"/{name}/":
                raise BundleError(f"{member.name}: path escapes the bundle")
            total += member.size
            if len(files) >= MAX_FILES or total > MAX_BUNDLE_BYTES:
                raise BundleError("bundle has too many files or bytes")
            files[name] = tar.extractfile(member).read()
    if "SKILL.md" not in files:
        raise BundleError("bundle has no SKILL.md at its root")
    return files
