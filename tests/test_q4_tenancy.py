"""Q4: each customer, team and user sees only their own skills, plus global ones."""
import pytest

from registry.bundle import pack_dir
from registry.demo import ACME_SALES, ACME_SUPPORT, EXAMPLES, GLOBEX_SUPPORT, PLATFORM
from registry.service import AccessDenied, NotFound, Principal


def names(registry, who):
    return sorted(c.name for c in registry.cards(registry.open_session(who)))


def test_each_tenant_gets_its_own_refund_policy(registry, ids):
    acme = registry.open_session(ACME_SUPPORT)
    globex = registry.open_session(GLOBEX_SUPPORT)
    assert "30 days" in registry.load(acme, ids["acme/refund-policy"])["instructions"]
    assert "14 days" in registry.load(globex, ids["globex/refund-policy"])["instructions"]


def test_other_tenants_skill_is_invisible_everywhere(registry, ids):
    globex = registry.open_session(GLOBEX_SUPPORT)
    acme_skill = ids["acme/refund-policy"]
    assert acme_skill not in {c.skill_id for c in registry.cards(globex)}
    assert acme_skill not in {h.skill_id for h in registry.search(globex, "acme refund 30-day")}
    with pytest.raises(NotFound):
        registry.load(globex, acme_skill)  # same error as a missing skill: no probing
    with pytest.raises(NotFound):
        registry.bundle_files(globex, acme_skill)


def test_team_scope(registry, ids):
    assert "crm-lookup" in names(registry, ACME_SALES)
    assert "crm-lookup" not in names(registry, ACME_SUPPORT)  # same tenant, other team


def test_user_scope(registry, ids, tmp_path):
    from .conftest import make_skill

    ana = ACME_SUPPORT
    root = make_skill(
        tmp_path, "meeting-notes-format",
        "Ana's personal format for meeting notes: decisions first, then owners and dates. "
        "Use when Ana asks to tidy or format meeting notes.",
        triggers=[("format these meeting notes my way", True), ("tidy up my meeting notes", True),
                  ("total this invoice with tax", False), ("profile this csv export", False),
                  ("what's the refund policy", False), ("meeting notes, decisions first please", True)])
    result = registry.publish(ana, pack_dir(root), scope="user")
    registry.promote(ana, result.skill_id, result.version)
    assert "meeting-notes-format" in names(registry, ana)
    assert "meeting-notes-format" not in names(registry, Principal("acme", "bob", "support"))


def test_global_skills_reach_every_tenant_and_only_platform_can_publish_them(registry, ids):
    for who in (ACME_SUPPORT, GLOBEX_SUPPORT):
        assert {"invoice-totals", "csv-profile", "release-notes"} <= set(names(registry, who))
    with pytest.raises(AccessDenied):
        registry.publish(ACME_SUPPORT, pack_dir(EXAMPLES / "skills/csv-profile"), scope="global")


def test_same_name_in_another_tenant_never_touches_the_original(registry, ids):
    # Globex publishing a skill called refund-policy adds a version to GLOBEX's skill.
    mine = registry.publish(GLOBEX_SUPPORT, pack_dir(EXAMPLES / "tenants/acme/refund-policy"))
    assert mine.skill_id == ids["globex/refund-policy"] and mine.version == 2
    acme = registry.db.execute("SELECT MAX(version) FROM versions WHERE skill_id = ?",
                               (ids["acme/refund-policy"],)).fetchone()[0]
    assert acme == 1


def test_sharing_is_a_grant_and_revoking_takes_effect_mid_session(registry, ids):
    crm = ids["acme/crm-lookup"]
    registry.share(ACME_SALES, crm, tenant="acme", team="support")
    support = registry.open_session(ACME_SUPPORT)
    assert "crm-lookup" in [c.name for c in registry.cards(support)]
    registry.load(support, crm)

    registry.revoke(ACME_SALES, crm, tenant="acme")
    assert "crm-lookup" not in [c.name for c in registry.cards(support)]
    with pytest.raises(NotFound):
        registry.load(support, crm)


def test_only_owner_scope_can_share_or_promote(registry, ids):
    with pytest.raises(AccessDenied):
        registry.share(GLOBEX_SUPPORT, ids["acme/refund-policy"], tenant="globex")
    with pytest.raises(AccessDenied):
        registry.promote(ACME_SUPPORT, ids["acme/crm-lookup"], 1)  # support is not the sales team
    with pytest.raises(AccessDenied):
        registry.rollback(ACME_SUPPORT, ids["platform/invoice-totals"], 1)
