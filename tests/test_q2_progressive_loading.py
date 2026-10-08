"""Q2: the agent finds and loads skills without a local disk, one level at a time."""
import pytest

from registry.demo import ACME_SUPPORT
from registry.search import Doc, HybridIndex
from registry.service import NotFound


def test_level1_cards_carry_no_instructions(registry, ids):
    session = registry.open_session(ACME_SUPPORT)
    cards = registry.cards(session)
    assert {c.name for c in cards} == {"invoice-totals", "csv-profile", "release-notes", "refund-policy"}
    for card in cards:
        assert "#" not in card.description  # no SKILL.md body leaks into level 1
        assert len(card.name) + len(card.description) < 400  # roughly 100 tokens or less


def test_level2_load_returns_body_and_file_list(registry, ids):
    session = registry.open_session(ACME_SUPPORT)
    loaded = registry.load(session, ids["platform/invoice-totals"])
    assert "scripts/total.py" in loaded["instructions"]
    assert loaded["files"] == ["evals/triggers.jsonl", "references/rounding.md", "scripts/total.py"]


def test_level3_reads_one_file_on_demand(registry, ids):
    session = registry.open_session(ACME_SUPPORT)
    text = registry.read_file(session, ids["platform/invoice-totals"], "references/rounding.md")
    assert "half-up" in text
    with pytest.raises(NotFound):
        registry.read_file(session, ids["platform/invoice-totals"], "../../etc/passwd")


def test_every_load_is_audited(registry, ids):
    session = registry.open_session(ACME_SUPPORT)
    registry.load(session, ids["platform/release-notes"])
    registry.read_file(session, ids["platform/release-notes"], "references/style.md")
    events = [e["event"] for e in registry.audit_log(session)]
    assert events == ["load", "read_file"]


def test_search_finds_exact_names_and_paraphrases(registry, ids):
    session = registry.open_session(ACME_SUPPORT)
    assert registry.search(session, "csv-profile")[0].name == "csv-profile"  # exact name
    assert registry.search(session, "check that a bill adds up")[0].name == "invoice-totals"  # paraphrase
    assert registry.search(session, "money back after cancelling")[0].name == "refund-policy"


def test_exact_name_beats_a_near_identical_neighbour():
    """Two invoice skills share most words; the exact name must still rank first."""
    docs = [
        Doc("a", "invoice-totals", "Add up invoice line items and apply tax."),
        Doc("b", "invoice-reminders", "Send reminders for unpaid invoices and totals due."),
    ]
    index = HybridIndex(docs)
    top = index.search("invoice totals")[0]
    assert top.skill_id == "a"
    assert top.bm25 > 0
