"""The registry: publish, promote, roll back, share, and serve skills to sessions.

Rules this file enforces:
- A published version is immutable (content-addressed bundle + no UPDATE of its bytes).
- Only an approved version can be the live one, and approval needs a passing trigger eval.
- A session pins the live version of every skill it can see when it opens. Promoting or
  rolling back later changes new sessions only, so a running agent never sees a skill change
  under it.
- Access is checked in SQL before anything reaches the agent, and again on every load.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field

from .bundle import BundleError, parse_skill_md, read_bundle, sha256
from .evals import load_cases, trigger_eval
from .scan import Finding, blocks_publish, scan_bundle
from .search import Doc, HybridIndex
from .store import ObjectStore, bundle_key

SCOPES = ("global", "tenant", "team", "user")
PLATFORM_TENANT = "platform"  # the operator; the only owner of global (built-in) skills

SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_tenant TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('global','tenant','team','user')),
    owner_team TEXT,
    owner_user TEXT,
    live_version INTEGER,
    UNIQUE (owner_tenant, name)
);
CREATE TABLE IF NOT EXISTS versions (
    skill_id TEXT NOT NULL REFERENCES skills(id),
    version INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    description TEXT NOT NULL,
    body TEXT NOT NULL,
    metadata TEXT NOT NULL,
    files TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','rejected','approved')),
    findings TEXT NOT NULL,
    eval TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (skill_id, version)
);
CREATE TABLE IF NOT EXISTS grants (
    skill_id TEXT NOT NULL REFERENCES skills(id),
    tenant TEXT NOT NULL,
    team TEXT,
    user TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, team TEXT, user TEXT NOT NULL, opened_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS session_pins (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    skill_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    PRIMARY KEY (session_id, skill_id)
);
CREATE TABLE IF NOT EXISTS audit (
    ts REAL NOT NULL, session_id TEXT, event TEXT NOT NULL, skill_id TEXT, version INTEGER, detail TEXT
);
"""

# Visibility in one place. Parameters: tenant, team, user, tenant, team, user.
VISIBLE_SQL = """
(
    s.scope = 'global'
    OR (s.owner_tenant = ? AND (
            s.scope = 'tenant'
         OR (s.scope = 'team' AND s.owner_team = ?)
         OR (s.scope = 'user' AND s.owner_user = ?)))
    OR EXISTS (SELECT 1 FROM grants g WHERE g.skill_id = s.id AND g.tenant = ?
               AND (g.team IS NULL OR g.team = ?) AND (g.user IS NULL OR g.user = ?))
)
"""


class AccessDenied(PermissionError):
    pass


class NotFound(LookupError):
    pass


class PromotionBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class Principal:
    tenant: str
    user: str
    team: str | None = None

    def params(self) -> tuple:
        return (self.tenant, self.team, self.user) * 2


@dataclass
class PublishResult:
    skill_id: str
    name: str
    version: int
    sha256: str
    status: str
    findings: list[Finding] = field(default_factory=list)


@dataclass(frozen=True)
class SkillCard:
    """Level 1 of progressive disclosure: what the agent sees before it picks."""
    skill_id: str
    name: str
    description: str
    version: int


class SkillRegistry:
    def __init__(self, store: ObjectStore, db_path: str = ":memory:") -> None:
        self.store = store
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    # ---------- publishing ----------

    def publish(self, who: Principal, bundle: bytes, scope: str = "tenant") -> PublishResult:
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        files = read_bundle(bundle)
        manifest = parse_skill_md(files["SKILL.md"].decode("utf-8"))
        findings = scan_bundle(files)
        status = "rejected" if blocks_publish(findings) else "draft"
        digest = sha256(bundle)
        owner_tenant = who.tenant
        if scope == "global" and who.tenant != PLATFORM_TENANT:
            raise AccessDenied("only the platform can publish global skills")
        if scope == "team" and not who.team:
            raise ValueError("team scope needs a team")

        with self.lock, self.db:
            row = self.db.execute(
                "SELECT * FROM skills WHERE owner_tenant = ? AND name = ?", (owner_tenant, manifest.name)
            ).fetchone()
            if row is None:
                skill_id = f"skill_{uuid.uuid4().hex[:12]}"
                self.db.execute(
                    "INSERT INTO skills (id, name, owner_tenant, scope, owner_team, owner_user) VALUES (?,?,?,?,?,?)",
                    (skill_id, manifest.name, owner_tenant, scope,
                     who.team if scope == "team" else None, who.user if scope == "user" else None),
                )
            else:
                skill_id = row["id"]
                self._check_can_write(who, row)
            version = 1 + self.db.execute(
                "SELECT COALESCE(MAX(version), 0) FROM versions WHERE skill_id = ?", (skill_id,)
            ).fetchone()[0]
            self.store.put(bundle_key(digest), bundle)
            self.db.execute(
                "INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,NULL,?)",
                (skill_id, version, digest, manifest.description, manifest.body,
                 json.dumps(manifest.metadata), json.dumps(sorted(files)), status,
                 json.dumps([f.__dict__ for f in findings]), time.time()),
            )
            self._audit(None, "publish", skill_id, version, f"{status} by {who.user}")
        return PublishResult(skill_id, manifest.name, version, digest, status, findings)

    def _check_can_write(self, who: Principal, row: sqlite3.Row) -> None:
        if row["scope"] == "global":
            if who.tenant != PLATFORM_TENANT:
                raise AccessDenied("only the platform can change global skills")
            return
        same_owner = row["owner_tenant"] == who.tenant and (
            row["scope"] == "tenant"
            or (row["scope"] == "team" and row["owner_team"] == who.team)
            or (row["scope"] == "user" and row["owner_user"] == who.user)
        )
        if not same_owner:
            raise AccessDenied("only the owner scope can publish new versions")

    def evaluate(self, who: Principal, skill_id: str, version: int) -> dict:
        """Run the version's own trigger cases against everything else `who` can see."""
        skill = self._skill_row(skill_id)
        self._check_can_write(who, skill)
        v = self._version_row(skill_id, version)
        files = read_bundle(self.store.get(bundle_key(v["sha256"])))
        rows = self.db.execute(
            f"""SELECT s.id, s.name, v.description FROM skills s
                JOIN versions v ON v.skill_id = s.id AND v.version = s.live_version
                WHERE {VISIBLE_SQL}""",
            who.params(),
        ).fetchall()
        neighbours = [Doc(r["id"], r["name"], r["description"]) for r in rows]
        candidate = Doc(skill_id, skill["name"], v["description"])
        return trigger_eval(skill_id, neighbours, candidate, load_cases(files))

    def promote(self, who: Principal, skill_id: str, version: int) -> dict:
        """Make a version live. Needs a clean scan and a passing trigger eval, both run here,
        so a caller cannot hand in its own 'passed' report."""
        with self.lock:
            skill = self._skill_row(skill_id)
            self._check_can_write(who, skill)
            if self._version_row(skill_id, version)["status"] == "rejected":
                raise PromotionBlocked("version failed the publish scan")
            report = self.evaluate(who, skill_id, version)
            with self.db:  # commit the report and audit row even when promotion is refused
                self.db.execute("UPDATE versions SET eval = ? WHERE skill_id = ? AND version = ?",
                                (json.dumps(report), skill_id, version))
                if report["passed"]:
                    self.db.execute(
                        "UPDATE versions SET status = 'approved' WHERE skill_id = ? AND version = ?",
                        (skill_id, version))
                    self.db.execute("UPDATE skills SET live_version = ? WHERE id = ?", (version, skill_id))
                    self._audit(None, "promote", skill_id, version, report["summary"])
                else:
                    self._audit(None, "promote_blocked", skill_id, version, report["summary"])
        if not report["passed"]:
            raise PromotionBlocked(f"trigger eval did not pass: {report['summary']}")
        return report

    def rollback(self, who: Principal, skill_id: str, version: int) -> None:
        with self.lock, self.db:
            skill = self._skill_row(skill_id)
            self._check_can_write(who, skill)
            if self._version_row(skill_id, version)["status"] != "approved":
                raise PromotionBlocked("can only roll back to an approved version")
            self.db.execute("UPDATE skills SET live_version = ? WHERE id = ?", (version, skill_id))
            self._audit(None, "rollback", skill_id, version, f"by {who.user}")

    def share(self, who: Principal, skill_id: str, tenant: str, team: str | None = None,
              user: str | None = None) -> None:
        """A grant, never a copy: the shared skill keeps one owner and one version history."""
        with self.lock, self.db:
            self._check_can_write(who, self._skill_row(skill_id))
            self.db.execute("INSERT INTO grants VALUES (?,?,?,?)", (skill_id, tenant, team, user))

    def revoke(self, who: Principal, skill_id: str, tenant: str) -> None:
        with self.lock, self.db:
            self._check_can_write(who, self._skill_row(skill_id))
            self.db.execute("DELETE FROM grants WHERE skill_id = ? AND tenant = ?", (skill_id, tenant))

    # ---------- sessions (what the agent touches) ----------

    def open_session(self, who: Principal) -> str:
        """Pin the live version of every visible skill. The pin set is the session's whole world."""
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        with self.lock, self.db:
            self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                            (session_id, who.tenant, who.team, who.user, time.time()))
            self.db.execute(
                f"""INSERT INTO session_pins SELECT ?, s.id, s.live_version FROM skills s
                    WHERE s.live_version IS NOT NULL AND {VISIBLE_SQL}""",
                (session_id, *who.params()),
            )
        return session_id

    def cards(self, session_id: str) -> list[SkillCard]:
        """Level 1: names and descriptions only. Bodies and files stay in storage."""
        who = self._session_principal(session_id)
        rows = self.db.execute(
            f"""SELECT s.id, s.name, v.description, v.version FROM session_pins p
                JOIN skills s ON s.id = p.skill_id
                JOIN versions v ON v.skill_id = p.skill_id AND v.version = p.version
                WHERE p.session_id = ? AND {VISIBLE_SQL} ORDER BY s.name""",
            (session_id, *who.params()),
        ).fetchall()
        return [SkillCard(r["id"], r["name"], r["description"], r["version"]) for r in rows]

    def search(self, session_id: str, query: str, k: int = 5):
        cards = self.cards(session_id)
        index = HybridIndex([Doc(c.skill_id, c.name, c.description) for c in cards])
        return index.search(query, k=k)

    def load(self, session_id: str, skill_id: str) -> dict:
        """Level 2: the SKILL.md body of the pinned version, plus the list of its files."""
        v = self._pinned_version(session_id, skill_id)
        with self.lock, self.db:
            self._audit(session_id, "load", skill_id, v["version"], None)
        return {
            "skill_id": skill_id,
            "version": v["version"],
            "instructions": v["body"],
            "files": [f for f in json.loads(v["files"]) if f != "SKILL.md"],
        }

    def bundle_files(self, session_id: str, skill_id: str) -> tuple[dict[str, bytes], dict[str, str], int]:
        """Level 3: the pinned bundle from object storage, hash-checked before use."""
        v = self._pinned_version(session_id, skill_id)
        data = self.store.get(bundle_key(v["sha256"]))
        if sha256(data) != v["sha256"]:
            raise BundleError("stored bundle does not match its recorded hash")
        return read_bundle(data), json.loads(v["metadata"]), v["version"]

    def read_file(self, session_id: str, skill_id: str, path: str, max_bytes: int = 20_000) -> str:
        files, _, version = self.bundle_files(session_id, skill_id)
        if path not in files or path == "SKILL.md":
            raise NotFound(f"{path} is not in this skill")
        with self.lock, self.db:
            self._audit(session_id, "read_file", skill_id, version, path)
        return files[path][:max_bytes].decode("utf-8", errors="replace")

    # ---------- helpers ----------

    def _pinned_version(self, session_id: str, skill_id: str) -> sqlite3.Row:
        who = self._session_principal(session_id)
        row = self.db.execute(
            f"""SELECT v.* FROM session_pins p
                JOIN skills s ON s.id = p.skill_id
                JOIN versions v ON v.skill_id = p.skill_id AND v.version = p.version
                WHERE p.session_id = ? AND p.skill_id = ? AND {VISIBLE_SQL}""",
            (session_id, skill_id, *who.params()),
        ).fetchone()
        if row is None:
            # Same answer for "does not exist" and "not yours": no probing other tenants.
            raise NotFound(f"skill {skill_id} is not available in this session")
        return row

    def principal(self, session_id: str) -> Principal:
        return self._session_principal(session_id)

    def _session_principal(self, session_id: str) -> Principal:
        row = self.db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise NotFound("unknown session")
        return Principal(tenant=row["tenant"], user=row["user"], team=row["team"])

    def _skill_row(self, skill_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if row is None:
            raise NotFound(skill_id)
        return row

    def _version_row(self, skill_id: str, version: int) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM versions WHERE skill_id = ? AND version = ?", (skill_id, version)
        ).fetchone()
        if row is None:
            raise NotFound(f"{skill_id} v{version}")
        return row

    def _audit(self, session_id, event, skill_id, version, detail) -> None:
        self.db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?)",
                        (time.time(), session_id, event, skill_id, version, detail))

    def audit_log(self, session_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM audit", ()
        if session_id:
            sql, args = sql + " WHERE session_id = ?", (session_id,)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY ts", args)]
