"""Q3: bundled scripts reach the sandbox from storage and run locked down.

These tests start real Docker containers and are skipped when Docker is not running."""
import json

import pytest

from registry.bundle import pack_dir, read_bundle
from registry.demo import ACME_SALES, ACME_SUPPORT, EXAMPLES
from registry.sandbox import DockerSandbox, SandboxError, SandboxPolicy
from registry.secrets import SecretVault

from .conftest import make_skill


def run_probe(sandbox, tmp_path, code, **kwargs):
    root = make_skill(tmp_path, "probe", "Probe the sandbox.", files={"scripts/probe.py": code})
    return sandbox.run(read_bundle(pack_dir(root)), {}, "scripts/probe.py", **kwargs)


def test_runs_the_pinned_script_with_stdin(registry, ids, sandbox):
    session = registry.open_session(ACME_SUPPORT)
    files, metadata, _ = registry.bundle_files(session, ids["platform/invoice-totals"])
    result = sandbox.run(files, metadata, "scripts/total.py", ["0.08"],
                         stdin="item,qty,unit_price\nwidget,2,9.99\ncable,3,4.50\nbad,x,1\n")
    assert result.exit_code == 0, result.stderr
    out = json.loads(result.stdout)
    assert out == {"lines": 2, "subtotal": "33.48", "tax": "2.68", "total": "36.16",
                   "bad_rows": [{"item": "bad", "qty": "x", "unit_price": "1"}]}


def test_bundle_is_read_only(sandbox, tmp_path):
    result = run_probe(sandbox, tmp_path, "open('/skill/SKILL.md', 'a').write('x')\n")
    assert result.exit_code != 0
    assert "Read-only file system" in result.stderr or "Permission denied" in result.stderr


def test_scratch_dir_is_writable_and_root_fs_is_not(sandbox, tmp_path):
    code = (
        "open('/work/out.txt', 'w').write('ok')\n"
        "try:\n    open('/etc/x', 'w')\nexcept OSError as e:\n    print('blocked', e.errno)\n"
    )
    result = run_probe(sandbox, tmp_path, code)
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("blocked")


def test_no_network(sandbox, tmp_path):
    code = (
        "import socket\n"
        "try:\n    socket.create_connection(('1.1.1.1', 53), timeout=2)\n    print('connected')\n"
        "except OSError:\n    print('no network')\n"
    )
    assert run_probe(sandbox, tmp_path, code).stdout.strip() == "no network"


def test_runs_as_non_root(sandbox, tmp_path):
    assert run_probe(sandbox, tmp_path, "import os; print(os.getuid())").stdout.strip() == "65534"


def test_runaway_script_is_killed(tmp_path):
    from registry.sandbox import docker_available

    if not docker_available():
        pytest.skip("Docker is not available")
    quick = DockerSandbox(SandboxPolicy(timeout_s=3))
    result = run_probe(quick, tmp_path, "while True:\n    pass\n")
    assert result.timed_out and result.exit_code == 124


def test_output_is_capped(sandbox, tmp_path):
    result = run_probe(sandbox, tmp_path, "print('x' * 100_000)")
    assert result.truncated and len(result.stdout) <= sandbox.policy.max_output_bytes


def test_secret_in_debug_log_is_scrubbed(registry, ids, sandbox):
    vault = SecretVault()
    vault.set("acme", "CRM_API_KEY", "crm-live-4f9a8b7c6d5e")
    session = registry.open_session(ACME_SALES)
    files, metadata, _ = registry.bundle_files(session, ids["acme/crm-lookup"])
    result = sandbox.run(files, metadata, "scripts/lookup.py", ["jane@northwind.com"],
                         secrets=vault.for_skill("acme", metadata))
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["owner"] == "Sam Lee"
    assert "crm-live-4f9a8b7c6d5e" not in result.stderr
    assert "key=[REDACTED]" in result.stderr and result.redactions == 1


def test_secret_value_never_on_the_docker_command_line():
    cmd = DockerSandbox().build_command("n", EXAMPLES, "scripts/a.py", [], ["CRM_API_KEY"], False)
    assert "CRM_API_KEY" in cmd and not any("=" in c and c.startswith("CRM_API_KEY") for c in cmd)
    assert cmd[cmd.index("--network") + 1] == "none"


def test_refuses_paths_outside_scripts_and_undeclared_secrets(tmp_path):
    files = read_bundle(pack_dir(make_skill(tmp_path, "probe", "Probe.", files={"scripts/a.py": ""})))
    with pytest.raises(SandboxError):
        DockerSandbox().run(files, {}, "SKILL.md")
    with pytest.raises(SandboxError):
        DockerSandbox().run(files, {}, "scripts/a.py", secrets={"AWS_SECRET": "x"})
