"""HTTP API. Callers identify with X-Tenant / X-User / X-Team headers here; in
production those come from a verified token (JWT claims), never from the client.

Run:  uvicorn registry.api:app --reload
Env:  SKILLS_DB (sqlite path, default in-memory), SKILLS_S3_BUCKET (+ SKILLS_S3_ENDPOINT
      for MinIO) to use object storage, SKILLS_SEED=1 to load the example skills.
"""
from __future__ import annotations

import os
from dataclasses import asdict

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .bundle import BundleError
from .sandbox import DockerSandbox, SandboxError
from .secrets import SecretVault
from .service import AccessDenied, NotFound, Principal, PromotionBlocked, SkillRegistry
from .store import MemoryStore, S3Store


class RunRequest(BaseModel):
    script: str
    args: list[str] = []
    stdin: str = ""


class GrantRequest(BaseModel):
    tenant: str
    team: str | None = None
    user: str | None = None


def principal(x_tenant: str = Header(...), x_user: str = Header(...),
              x_team: str | None = Header(None)) -> Principal:
    return Principal(tenant=x_tenant, user=x_user, team=x_team)


def create_app(registry: SkillRegistry, sandbox: DockerSandbox | None = None,
               vault: SecretVault | None = None) -> FastAPI:
    app = FastAPI(title="Cloud Skill Registry")
    vault = vault or SecretVault()

    errors = {NotFound: 404, AccessDenied: 403, PromotionBlocked: 409,
              BundleError: 422, ValueError: 422, SandboxError: 400}
    for exc, code in errors.items():
        app.add_exception_handler(exc, lambda _r, e, code=code: JSONResponse({"error": str(e)}, code))

    def own_session(session_id: str, who: Principal) -> str:
        owner = registry.principal(session_id)
        if (owner.tenant, owner.user) != (who.tenant, who.user):
            raise NotFound("unknown session")  # never confirm another caller's session exists
        return session_id

    @app.post("/v1/skills")
    async def publish(request: Request, scope: str = "tenant", who: Principal = Depends(principal)):
        result = registry.publish(who, await request.body(), scope=scope)
        return {**asdict(result), "findings": [asdict(f) for f in result.findings]}

    @app.post("/v1/skills/{skill_id}/versions/{version}/promote")
    def promote(skill_id: str, version: int, who: Principal = Depends(principal)):
        return registry.promote(who, skill_id, version)

    @app.post("/v1/skills/{skill_id}/rollback/{version}")
    def rollback(skill_id: str, version: int, who: Principal = Depends(principal)):
        registry.rollback(who, skill_id, version)
        return {"skill_id": skill_id, "live_version": version}

    @app.post("/v1/skills/{skill_id}/grants")
    def share(skill_id: str, grant: GrantRequest, who: Principal = Depends(principal)):
        registry.share(who, skill_id, grant.tenant, grant.team, grant.user)
        return {"skill_id": skill_id, "granted_to": grant.model_dump()}

    @app.post("/v1/sessions")
    def open_session(who: Principal = Depends(principal)):
        session_id = registry.open_session(who)
        return {"session_id": session_id, "skills": [asdict(c) for c in registry.cards(session_id)]}

    @app.get("/v1/sessions/{session_id}/skills")
    def cards(session_id: str, who: Principal = Depends(principal)):
        return [asdict(c) for c in registry.cards(own_session(session_id, who))]

    @app.get("/v1/sessions/{session_id}/search")
    def search(session_id: str, q: str, who: Principal = Depends(principal)):
        return [asdict(h) for h in registry.search(own_session(session_id, who), q)]

    @app.get("/v1/sessions/{session_id}/skills/{skill_id}")
    def load(session_id: str, skill_id: str, who: Principal = Depends(principal)):
        return registry.load(own_session(session_id, who), skill_id)

    @app.get("/v1/sessions/{session_id}/skills/{skill_id}/files/{path:path}")
    def read_file(session_id: str, skill_id: str, path: str, who: Principal = Depends(principal)):
        return {"path": path, "content": registry.read_file(own_session(session_id, who), skill_id, path)}

    @app.post("/v1/sessions/{session_id}/skills/{skill_id}/run")
    def run(session_id: str, skill_id: str, req: RunRequest = Body(...), who: Principal = Depends(principal)):
        if sandbox is None:
            raise HTTPException(503, "no sandbox configured")
        files, metadata, version = registry.bundle_files(own_session(session_id, who), skill_id)
        result = sandbox.run(files, metadata, req.script, req.args, req.stdin,
                             secrets=vault.for_skill(who.tenant, metadata))
        return {"version": version, **asdict(result)}

    return app


def _app_from_env() -> FastAPI:
    store = S3Store() if os.environ.get("SKILLS_S3_BUCKET") else MemoryStore()
    registry = SkillRegistry(store, os.environ.get("SKILLS_DB", ":memory:"))
    if os.environ.get("SKILLS_SEED") == "1":
        from .demo import seed

        seed(registry)
    return create_app(registry, DockerSandbox())


app = _app_from_env()
