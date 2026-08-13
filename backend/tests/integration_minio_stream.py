"""Opt-in MinIO streaming and cross-tenant isolation smoke test.

This script is intentionally not part of the default unit-test suite. Run it
inside a container on the Compose network with the TEST_* variables supplied.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.object_storage import MinioObjectStore, ObjectStorageError
from app.tenancy import TenantBinding


def _assert_bucket_denied(store: MinioObjectStore, bucket: str) -> None:
    """Force an S3 list request; a lazy iterator alone does not hit MinIO."""
    try:
        list(store.client.list_objects(bucket, recursive=False))
    except Exception as exc:
        code = str(getattr(exc, "code", ""))
        if code in {"AccessDenied", "NoSuchBucket"} or "Access Denied" in str(exc):
            return
        raise RuntimeError(f"unexpected MinIO isolation error for {bucket}: {exc}") from exc
    raise RuntimeError(f"MinIO credentials unexpectedly listed protected bucket: {bucket}")


def _store(prefix: str, institution_id: str) -> MinioObjectStore:
    return MinioObjectStore(TenantBinding(
        institution_id=institution_id,
        display_name=institution_id,
        status="active",
        database_url="",
        migration_database_url="",
        storage_backend="minio",
        object_endpoint=os.environ["TEST_MINIO_ENDPOINT"],
        data_bucket=os.environ[f"TEST_{prefix}_BUCKET"],
        object_access_key=os.environ[f"TEST_{prefix}_ACCESS_KEY"],
        object_secret_key=os.environ[f"TEST_{prefix}_SECRET_KEY"],
    ))


def main() -> None:
    org_a = _store("ORG_A", "org-a")
    org_b = _store("ORG_B", "org-b")
    _assert_bucket_denied(org_a, os.environ["TEST_ORG_B_BUCKET"])
    _assert_bucket_denied(org_b, os.environ["TEST_ORG_A_BUCKET"])
    _assert_bucket_denied(org_a, os.environ["TEST_ORG_A_BACKUP_BUCKET"])
    _assert_bucket_denied(org_b, os.environ["TEST_ORG_B_BACKUP_BUCKET"])
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "stream-source.bin"
        destination = root / "stream-copy.bin"
        with source.open("wb") as handle:
            for _ in range(16):
                handle.write(b"longyun-minio-stream-test" * 65536)
        stored = org_a.put_file("projects/integration/stream/source.bin", source)
        try:
            org_a.copy_to(stored.locator, destination)
            if destination.read_bytes() != source.read_bytes():
                raise RuntimeError("streamed MinIO object differs from its source")
            try:
                org_b.copy_to(stored.locator, root / "denied.bin")
            except ObjectStorageError:
                pass
            else:
                raise RuntimeError("org-b accepted an org-a object locator")
        finally:
            org_a.delete(stored.locator)
    print("minio-streaming-cross-tenant-and-backup-isolation-ok")


if __name__ == "__main__":
    main()
