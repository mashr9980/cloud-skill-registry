# Cloud Skill Registry

A central registry that stores, scopes, versions and serves agent skills (`SKILL.md` packages) to LLM agents running in the cloud. Scripts run in a locked-down sandbox, and no skill goes live without passing a trigger-accuracy eval.

Skills follow the open [Agent Skills format](https://agentskills.io/specification): a folder with `SKILL.md` (YAML frontmatter + instructions) and optional `scripts/`, `references/`, `assets/`.

```
45 tests, all passing: registry, tenancy, versioning, eval gate, HTTP API, agent loop,
and real Docker sandbox runs. Plus one test against a live S3 API (SeaweedFS).
```

## Five questions a cloud skills system has to answer

| Question | Answer in this repo | Code | Proof |
|---|---|---|---|
| **1. Where do skills live, if not on the user's machine?** | Metadata, versions, grants and pins in a database. Bundles in object storage (S3 API), keyed by their sha256, so a stored version can't change without its key changing. | [`registry/store.py`](registry/store.py), [`registry/bundle.py`](registry/bundle.py) | [`test_q1_central_store.py`](tests/test_q1_central_store.py), [`test_s3_store.py`](tests/test_s3_store.py) |
| **2. How does an agent find and load the right skill with no local disk?** | Progressive loading over four tools. Level 1: names + descriptions in the system prompt (or `search_skills` once there are more than 30). Level 2: `load_skill` returns the pinned `SKILL.md` body. Level 3: `read_skill_file` / `run_skill_script` pull one file or script on demand. Search is hybrid: BM25 for exact names, vectors for paraphrases, merged by rank. | [`agent/tools.py`](agent/tools.py), [`agent/loop.py`](agent/loop.py), [`registry/search.py`](registry/search.py) | [`test_q2_progressive_loading.py`](tests/test_q2_progressive_loading.py), [`test_agent_loop.py`](tests/test_agent_loop.py) |
| **3. How do bundled scripts get into the sandbox, and run safely?** | The pinned bundle is pulled from storage, hash-checked, and mounted **read-only** in a fresh container: no network, no capabilities, non-root, `no-new-privileges`, caps on time, memory, PIDs and output, one small scratch dir, removed after the run. Secrets go in by name only, and are scrubbed from stdout/stderr before the model sees them. | [`registry/sandbox.py`](registry/sandbox.py), [`registry/secrets.py`](registry/secrets.py) | [`test_q3_sandbox.py`](tests/test_q3_sandbox.py) (real containers) |
| **4. How does each customer or team see only their own skills?** | Every skill has a scope: `global`, `tenant`, `team` or `user`. Visibility is one SQL predicate applied before anything reaches the prompt, and again on every load. Sharing adds a grant, never a copy. Revoking takes effect mid-session. A skill that isn't yours returns the same 404 as one that doesn't exist. | [`registry/service.py`](registry/service.py) (`VISIBLE_SQL`) | [`test_q4_tenancy.py`](tests/test_q4_tenancy.py), [`test_api.py`](tests/test_api.py) |
| **5. How do updates ship without breaking running agents?** | Published versions are immutable. A session pins the live version of every skill when it opens, so promotions and rollbacks only affect new sessions. Promotion needs a clean publish-time scan **and** a passing trigger eval, both run by the registry, not trusted from the caller. Rollback moves one pointer. | [`registry/service.py`](registry/service.py), [`registry/evals.py`](registry/evals.py), [`registry/scan.py`](registry/scan.py) | [`test_q5_versioning.py`](tests/test_q5_versioning.py) |

## How it fits together

```mermaid
flowchart LR
    A[Author publishes bundle] --> S{Publish scan}
    S -- high-severity finding --> R[rejected, can never go live]
    S -- clean --> D[draft version]
    D --> E{Trigger eval}
    E -- fails --> D
    E -- passes --> L[live pointer]
    L --> P[Session opens: pins live versions it can see]
    P --> C[Agent: names + descriptions only]
    C -- load_skill --> B[SKILL.md body of pinned version]
    B -- run_skill_script --> X[Read-only bundle in sandbox, output scrubbed]
```

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev,s3,agent]"
.venv/bin/pytest                                   # 45 tests; sandbox tests need Docker running

docker compose up -d s3                            # S3-compatible storage on :8333
SKILLS_S3_ENDPOINT=http://localhost:8333 AWS_ACCESS_KEY_ID=any AWS_SECRET_ACCESS_KEY=any \
  AWS_DEFAULT_REGION=us-east-1 .venv/bin/pytest tests/test_s3_store.py

SKILLS_SEED=1 .venv/bin/uvicorn registry.api:app   # HTTP API with the example skills loaded
.venv/bin/python -m evals.run_trigger_eval         # trigger accuracy per skill, offline
.venv/bin/python -m evals.run_trigger_eval --live  # same cases, Claude decides (needs API credentials)
```

Example HTTP calls (identity headers stand in for a verified token):

```bash
curl -X POST localhost:8000/v1/sessions -H 'X-Tenant: acme' -H 'X-User: ana' -H 'X-Team: support'
curl "localhost:8000/v1/sessions/$SID/search?q=does%20this%20bill%20add%20up" -H 'X-Tenant: acme' -H 'X-User: ana'
curl -X POST localhost:8000/v1/sessions/$SID/skills/$SKILL/run -H 'X-Tenant: acme' -H 'X-User: ana' \
  -H 'content-type: application/json' \
  -d '{"script":"scripts/total.py","args":["0.08"],"stdin":"item,qty,unit_price\nwidget,2,9.99\n"}'
```

## Example skills

| Skill | Scope | Shows |
|---|---|---|
| `invoice-totals` | global | script + reference file; Decimal money math |
| `csv-profile` | global | script that reads stdin |
| `release-notes` | global | instructions only, with a style reference loaded on demand |
| `refund-policy` (Acme) / `refund-policy` (Globex) | tenant | same name, two tenants, never mixed |
| `crm-lookup` | team (Acme sales) | declared secret; its debug line prints the key and the sandbox scrubs it |

Every skill ships `evals/triggers.jsonl`: prompts that should fire it, and near-misses that shouldn't.

## Decisions, and why

- **Pin per session, not per load.** A session that loaded v1 of one skill and v2 of another mid-task would be running a combination nobody tested. The pin set is the session's whole world.
- **Content-addressed bundles.** Immutability comes from the storage key, not from a policy someone can forget.
- **The registry runs the eval itself.** If promotion accepted a `passed: true` from the caller, the gate would be decorative.
- **Undertriggering is the costly failure.** A skill that never fires can't help. `test_promotion_blocked_when_a_description_edit_stops_the_skill_firing` is the regression this catches.
- **Scan the text, not just the code.** In a 2026 study of 31,132 public skills, 26% had a vulnerability, and most sat in the natural-language instructions ([arXiv 2601.10338](https://arxiv.org/abs/2601.10338)).
- **Scrub output at the sandbox boundary.** Debug logging was the main credential-leak path in skills, because tool output flows straight into the model's context ([arXiv 2604.03070](https://arxiv.org/abs/2604.03070)).
- **Search past 30 skills.** Listing every description stops scaling; Anthropic reported tool-selection accuracy rising from 49% to 74% on Opus 4 when tools were searched instead of all listed ([Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)).

## What stands in for production here

Kept small on purpose so every part can be read in one sitting:

- **SQLite** stands in for Postgres. The SQL is plain and ports directly.
- **Header identity** stands in for verified JWT claims.
- **In-memory secrets** stand in for a secrets manager (AWS Secrets Manager, Vault).
- **Docker (runc)** is what the tests run. Docker shares the host kernel; for code the agent writes itself, set `SKILLS_SANDBOX_RUNTIME=runsc` (gVisor) or use a microVM runtime on hosts that have one. Those runtimes are not exercised by this test suite.
- **The offline eval** measures the retrieval router. `--live` measures what Claude actually loads; it needs API credentials and was not part of the test run.
- **MCP**: the four agent tools map one-to-one onto MCP tools, but no MCP server is included yet.

## Layout

```
registry/   bundle, store, scan, search, evals, service (core rules), sandbox, secrets, api, demo
agent/      tool definitions + Claude tool-use loop
evals/      trigger-accuracy runner (offline and --live)
examples/   skills/ (global) and tenants/<tenant>/ (scoped)
tests/      one file per question above, plus API, agent loop and S3
```
