"""Object storage for bundles. Keys are content hashes, so writes are idempotent
and a stored bundle is immutable. The agent and the sandbox never read the
registry's disk; they get bundles through this interface."""
from __future__ import annotations

import os
from typing import Protocol


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


class MemoryStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self._objects.setdefault(key, data)

    def get(self, key: str) -> bytes:
        return self._objects[key]

    def exists(self, key: str) -> bool:
        return key in self._objects


class S3Store:
    """Any S3 API: AWS S3, MinIO, Cloudflare R2. Configured from env:
    SKILLS_S3_BUCKET, optional SKILLS_S3_ENDPOINT (for MinIO/R2), plus standard AWS credentials."""

    def __init__(self, bucket: str | None = None, endpoint_url: str | None = None) -> None:
        import boto3

        self.bucket = bucket or os.environ["SKILLS_S3_BUCKET"]
        self.s3 = boto3.client("s3", endpoint_url=endpoint_url or os.environ.get("SKILLS_S3_ENDPOINT"))

    def put(self, key: str, data: bytes) -> None:
        if not self.exists(key):
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise


def bundle_key(digest: str) -> str:
    return f"bundles/{digest[:2]}/{digest}.tar.gz"
