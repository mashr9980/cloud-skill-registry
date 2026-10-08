"""Runs against real S3-compatible storage when SKILLS_S3_ENDPOINT is set
(e.g. `docker compose up -d s3`). Skipped otherwise."""
import os
import uuid

import pytest

from registry.bundle import pack_dir
from registry.demo import ACME_SUPPORT, EXAMPLES
from registry.service import SkillRegistry

pytestmark = pytest.mark.skipif(not os.environ.get("SKILLS_S3_ENDPOINT"), reason="no S3 endpoint")


@pytest.fixture
def s3_registry():
    import boto3

    from registry.store import S3Store

    bucket = f"skills-test-{uuid.uuid4().hex[:8]}"
    boto3.client("s3", endpoint_url=os.environ["SKILLS_S3_ENDPOINT"]).create_bucket(Bucket=bucket)
    return SkillRegistry(S3Store(bucket=bucket))


def test_publish_promote_load_through_s3(s3_registry):
    result = s3_registry.publish(ACME_SUPPORT, pack_dir(EXAMPLES / "tenants/acme/refund-policy"))
    s3_registry.promote(ACME_SUPPORT, result.skill_id, result.version)
    session = s3_registry.open_session(ACME_SUPPORT)
    files, _, _ = s3_registry.bundle_files(session, result.skill_id)
    assert "SKILL.md" in files
    assert s3_registry.store.exists(f"bundles/{result.sha256[:2]}/{result.sha256}.tar.gz")
