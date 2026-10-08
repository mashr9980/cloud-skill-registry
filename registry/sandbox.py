"""Run a skill's script in a throwaway container.

The bundle comes from object storage (hash-checked by the registry), is written to
a temp dir, and mounted READ-ONLY at /skill. The script gets one small writable
scratch dir, no network unless policy allows it, no Linux capabilities, a non-root
user, and caps on time, memory, processes and output. The container is removed
after every run.

Docker shares the host kernel. That is fine for reviewed skill scripts; for code
the agent writes itself, set `runtime` to a stronger one (gVisor's `runsc`, or a
microVM runtime such as Kata/Firecracker) on hosts that have it installed.

Secrets go in as env vars by NAME only (`-e NAME`), so values never appear in the
docker command line, and every value is scrubbed from stdout/stderr before the
model sees it. Debug logging was the main way skills leaked credentials in a 2026
study (arXiv 2604.03070), because tool output flows straight into the context.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

INTERPRETERS = {".py": ["python", "-I", "-B"], ".sh": ["sh"]}
SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b(sk|rk)-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)bearer\s+[a-z0-9._-]{16,}"),
]


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxPolicy:
    image: str = os.environ.get("SKILLS_SANDBOX_IMAGE", "python:3.13-slim-trixie")
    runtime: str | None = os.environ.get("SKILLS_SANDBOX_RUNTIME") or None  # e.g. "runsc"
    timeout_s: float = 10.0
    memory: str = "256m"
    cpus: str = "0.5"
    pids: int = 64
    scratch_mb: int = 16
    max_output_bytes: int = 8_000
    allow_egress: bool = False  # platform-wide switch; a skill must also declare network: egress


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    redactions: int


def redact(text: str, secret_values: list[str]) -> tuple[str, int]:
    count = 0
    for value in sorted((v for v in secret_values if len(v) >= 6), key=len, reverse=True):
        count += text.count(value)
        text = text.replace(value, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        text, n = pattern.subn("[REDACTED]", text)
        count += n
    return text, count


class DockerSandbox:
    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self.policy = policy or SandboxPolicy()

    def build_command(self, name: str, mount: Path, script: str, args: list[str],
                      secret_names: list[str], network: bool) -> list[str]:
        p = self.policy
        cmd = [
            "docker", "run", "--rm", "-i", "--name", name,
            "--network", "bridge" if network else "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "--pids-limit", str(p.pids),
            "--memory", p.memory, "--memory-swap", p.memory,
            "--cpus", p.cpus,
            "--tmpfs", f"/work:rw,noexec,nosuid,size={p.scratch_mb}m,mode=1777",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=4m,mode=1777",
            "-v", f"{mount}:/skill:ro",
            "-w", "/work",
            "-e", "HOME=/work",
        ]
        if p.runtime:
            cmd += ["--runtime", p.runtime]
        for secret in secret_names:
            cmd += ["-e", secret]  # name only: docker reads the value from our env
        interpreter = INTERPRETERS[Path(script).suffix]
        return cmd + [p.image, *interpreter, f"/skill/{script}", *args]

    def run(self, files: dict[str, bytes], metadata: dict[str, str], script: str,
            args: list[str] | None = None, stdin: str = "",
            secrets: dict[str, str] | None = None) -> RunResult:
        args = [str(a) for a in (args or [])]
        secrets = secrets or {}
        if not script.startswith("scripts/") or script not in files:
            raise SandboxError(f"{script!r} is not a script in this skill")
        if Path(script).suffix not in INTERPRETERS:
            raise SandboxError(f"no interpreter allowed for {script!r}")
        declared = set(filter(None, metadata.get("secrets", "").split()))
        if set(secrets) - declared:
            raise SandboxError("secrets passed that the skill did not declare")
        network = self.policy.allow_egress and metadata.get("network") == "egress"

        workdir = Path(tempfile.mkdtemp(prefix="skill-"))
        name = f"skill-run-{uuid.uuid4().hex[:12]}"
        try:
            for rel, data in files.items():
                target = workdir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o444)
            for d in sorted({p.parent for p in workdir.rglob("*")}, reverse=True):
                d.chmod(0o555)
            cmd = self.build_command(name, workdir, script, args, sorted(secrets), network)
            env = {**os.environ, **secrets}
            try:
                proc = subprocess.run(cmd, input=stdin.encode(), capture_output=True, env=env,
                                      timeout=self.policy.timeout_s)
                exit_code, out, err, timed_out = proc.returncode, proc.stdout, proc.stderr, False
            except subprocess.TimeoutExpired as exc:
                subprocess.run(["docker", "kill", name], capture_output=True)
                exit_code, out, err, timed_out = 124, exc.stdout or b"", exc.stderr or b"", True
        finally:
            for p in [workdir, *workdir.rglob("*")]:
                if p.is_dir():
                    p.chmod(0o755)
            shutil.rmtree(workdir, ignore_errors=True)

        # Redact the full text first, then cut: cutting first could split a secret
        # and let its first half through.
        stdout, n1 = redact(out.decode("utf-8", errors="replace"), list(secrets.values()))
        stderr, n2 = redact(err.decode("utf-8", errors="replace"), list(secrets.values()))
        limit = self.policy.max_output_bytes
        truncated = len(stdout) > limit or len(stderr) > limit
        return RunResult(exit_code, stdout[:limit], stderr[:limit], timed_out, truncated, n1 + n2)


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
