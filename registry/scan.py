"""Publish-time scan. A version with a high-severity finding is rejected and can
never be promoted. Instructions are scanned as well as scripts: a large 2026
study of public skills (arXiv 2601.10338) found most flaws sit in the
natural-language text, not the code."""
from __future__ import annotations

import re
from dataclasses import dataclass

RULES: list[tuple[str, str, str, re.Pattern[str]]] = [
    # (rule, severity, applies_to, pattern)
    ("hardcoded-aws-key", "high", "any", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("hardcoded-api-key", "high", "any", re.compile(r"\b(sk|rk)-[A-Za-z0-9_-]{20,}")),
    ("private-key", "high", "any", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("pipe-to-shell", "high", "any", re.compile(r"(curl|wget)[^\n|]*\|\s*(ba|z)?sh\b")),
    ("encoded-exec", "high", "any", re.compile(r"(base64\s+-d[^\n]*\|\s*(ba)?sh|exec\(\s*base64|eval\(\s*base64)")),
    ("env-dump", "medium", "script", re.compile(r"(print\(\s*os\.environ\s*\)|\bprintenv\b|^\s*env\s*$)", re.M)),
    ("override-instructions", "high", "text", re.compile(r"ignore (all |any )?(previous|prior|above) instructions", re.I)),
    ("hide-from-user", "high", "text", re.compile(r"(do not|don't|never) (tell|inform|show) the user", re.I)),
    ("send-data-out", "high", "text", re.compile(r"(send|post|upload|forward)\b[^\n]{0,60}\b(to|at)\s+https?://", re.I)),
]

TEXT_SUFFIXES = (".md", ".txt", ".json", ".yaml", ".yml", ".jsonl", ".csv")
SCRIPT_SUFFIXES = (".py", ".sh", ".js", ".ts")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    file: str
    line: int
    snippet: str


def scan_bundle(files: dict[str, bytes]) -> list[Finding]:
    findings: list[Finding] = []
    for path, data in sorted(files.items()):
        kind = "script" if path.endswith(SCRIPT_SUFFIXES) else "text" if path.endswith(TEXT_SUFFIXES) else None
        if kind is None:
            continue
        text = data.decode("utf-8", errors="replace")
        for rule, severity, applies_to, pattern in RULES:
            if applies_to not in ("any", kind):
                continue
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(Finding(rule, severity, path, line, match.group(0)[:80]))
    return findings


def blocks_publish(findings: list[Finding]) -> bool:
    return any(f.severity == "high" for f in findings)
