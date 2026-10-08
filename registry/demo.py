"""Load the example skills into a registry: three global built-ins, a refund
policy for each of two tenants, and a CRM lookup only Acme's sales team can see."""
from __future__ import annotations

from pathlib import Path

from .bundle import pack_dir
from .service import Principal, SkillRegistry

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

PLATFORM = Principal("platform", "ops")
ACME_SUPPORT = Principal("acme", "ana", "support")
ACME_SALES = Principal("acme", "sam", "sales")
GLOBEX_SUPPORT = Principal("globex", "gil", "support")

LAYOUT = [
    (PLATFORM, "global", "skills/invoice-totals"),
    (PLATFORM, "global", "skills/csv-profile"),
    (PLATFORM, "global", "skills/release-notes"),
    (ACME_SUPPORT, "tenant", "tenants/acme/refund-policy"),
    (GLOBEX_SUPPORT, "tenant", "tenants/globex/refund-policy"),
    (ACME_SALES, "team", "tenants/acme/crm-lookup"),
]


def seed(registry: SkillRegistry) -> dict[str, str]:
    """Publish and promote every example. Returns '<owner_tenant>/<name>' -> skill_id."""
    ids = {}
    for who, scope, rel in LAYOUT:
        result = registry.publish(who, pack_dir(EXAMPLES / rel), scope=scope)
        registry.promote(who, result.skill_id, result.version)
        ids[f"{who.tenant}/{result.name}"] = result.skill_id
    return ids
