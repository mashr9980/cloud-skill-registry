"""Per-tenant secrets. In-memory here; in production this is a secrets manager
(AWS Secrets Manager, Vault) keyed by tenant. A skill gets only the names it
declares in `metadata.secrets`, and only from its caller's tenant."""
from __future__ import annotations


class SecretVault:
    def __init__(self) -> None:
        self._by_tenant: dict[str, dict[str, str]] = {}

    def set(self, tenant: str, name: str, value: str) -> None:
        self._by_tenant.setdefault(tenant, {})[name] = value

    def for_skill(self, tenant: str, metadata: dict[str, str]) -> dict[str, str]:
        wanted = metadata.get("secrets", "").split()
        have = self._by_tenant.get(tenant, {})
        return {name: have[name] for name in wanted if name in have}
