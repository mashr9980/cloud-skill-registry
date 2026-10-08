"""Q1: skills live centrally (database + object storage), not on anyone's machine."""
from registry.bundle import pack_dir, read_bundle, sha256
from registry.demo import EXAMPLES, PLATFORM
from registry.store import bundle_key


def test_publish_stores_bundle_by_content_hash(registry, store):
    bundle = pack_dir(EXAMPLES / "skills/invoice-totals")
    result = registry.publish(PLATFORM, bundle, scope="global")
    assert result.sha256 == sha256(bundle)
    assert store.get(bundle_key(result.sha256)) == bundle


def test_packing_is_deterministic(tmp_path):
    a = pack_dir(EXAMPLES / "skills/csv-profile")
    b = pack_dir(EXAMPLES / "skills/csv-profile")
    assert a == b  # same folder, same bytes, same storage key


def test_session_gets_files_from_the_store_not_the_local_folder(registry, ids, tmp_path):
    from registry.demo import ACME_SUPPORT

    session = registry.open_session(ACME_SUPPORT)
    files, _, _ = registry.bundle_files(session, ids["platform/invoice-totals"])
    assert "scripts/total.py" in files
    assert files == read_bundle(pack_dir(EXAMPLES / "skills/invoice-totals"))


def test_tampered_bundle_is_refused(registry, ids, store):
    from registry.bundle import BundleError
    from registry.demo import ACME_SUPPORT
    import pytest

    session = registry.open_session(ACME_SUPPORT)
    skill_id = ids["platform/csv-profile"]
    digest = registry.db.execute(
        "SELECT sha256 FROM versions WHERE skill_id = ?", (skill_id,)).fetchone()[0]
    store._objects[bundle_key(digest)] = pack_dir(EXAMPLES / "skills/release-notes")  # swap the bytes
    with pytest.raises(BundleError):
        registry.bundle_files(session, skill_id)
