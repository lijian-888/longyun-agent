"""Institution-scoped object storage with local and MinIO implementations."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import ContextManager, Iterator, Protocol
from urllib.parse import urlparse

from .tenancy import TenantBinding, normalize_institution_id, tenant_database_manager


OBJECT_SEGMENT_PATTERN = re.compile(r"[^A-Za-z0-9._()\-\u4e00-\u9fff]+")


class ObjectStorageError(RuntimeError):
    pass


def safe_object_segment(value: str, fallback: str = "object") -> str:
    cleaned = OBJECT_SEGMENT_PATTERN.sub("_", (value or "").strip()).strip("._")
    return (cleaned or fallback)[:180]


def safe_object_key(value: str) -> str:
    path = PurePosixPath((value or "").replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ObjectStorageError("unsafe object key")
    parts = [safe_object_segment(part) for part in path.parts if part not in {"", "."}]
    if not parts:
        raise ObjectStorageError("empty object key")
    return "/".join(parts)


def project_object_key(
    *,
    project_id: str | None,
    category: str,
    resource_id: str,
    file_name: str,
    owner_user_id: str | None = None,
) -> str:
    project = safe_object_segment(project_id or "institution-shared")
    parts = ["projects", project, safe_object_segment(category), safe_object_segment(resource_id)]
    if owner_user_id:
        parts.extend(["users", safe_object_segment(owner_user_id)])
    parts.append(safe_object_segment(file_name, "upload.bin"))
    return "/".join(parts)


@dataclass(frozen=True)
class StoredObject:
    locator: str
    bucket: str
    object_key: str
    size_bytes: int
    sha256: str
    kms_key_id: str = ""


class ObjectStore(Protocol):
    def put_bytes(self, object_key: str, content: bytes, content_type: str) -> StoredObject: ...
    def put_file(self, object_key: str, source: Path, content_type: str) -> StoredObject: ...
    def read_bytes(self, locator: str) -> bytes: ...
    def copy_to(self, locator: str, destination: Path) -> Path: ...
    def delete(self, locator: str) -> None: ...
    def exists(self, locator: str) -> bool: ...
    def materialize(self, locator: str, suffix: str = "") -> ContextManager[Path]: ...


class LocalObjectStore:
    def __init__(
        self,
        institution_id: str,
        root: Path,
        legacy_roots: tuple[Path, ...] = (),
    ) -> None:
        self.institution_id = normalize_institution_id(institution_id)
        self.root = (root / self.institution_id).resolve()
        self.legacy_roots = tuple(item.resolve() for item in legacy_roots)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, object_key: str) -> Path:
        candidate = (self.root / safe_object_key(object_key)).resolve()
        if self.root not in candidate.parents:
            raise ObjectStorageError("object path escaped institution root")
        return candidate

    def put_bytes(self, object_key: str, content: bytes, content_type: str = "application/octet-stream") -> StoredObject:
        del content_type
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(content)
        temporary.replace(path)
        return StoredObject(
            locator=str(path),
            bucket=self.institution_id,
            object_key=safe_object_key(object_key),
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def put_file(
        self,
        object_key: str,
        source: Path,
        content_type: str = "application/octet-stream",
    ) -> StoredObject:
        del content_type
        source = source.resolve()
        if not source.is_file():
            raise ObjectStorageError("source file does not exist")
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        shutil.copyfile(source, temporary)
        temporary.replace(path)
        return StoredObject(
            locator=str(path),
            bucket=self.institution_id,
            object_key=safe_object_key(object_key),
            size_bytes=path.stat().st_size,
            sha256=_file_sha256(path),
        )

    def _locator_path(self, locator: str) -> Path:
        if locator.startswith("file://"):
            path = Path(urlparse(locator).path)
        else:
            path = Path(locator)
        path = path.resolve()
        allowed = self.root in path.parents or any(
            legacy == path or legacy in path.parents for legacy in self.legacy_roots
        )
        if not allowed:
            raise ObjectStorageError("file does not belong to institution storage")
        return path

    def read_bytes(self, locator: str) -> bytes:
        return self._locator_path(locator).read_bytes()

    def copy_to(self, locator: str, destination: Path) -> Path:
        source = self._locator_path(locator)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
        return destination

    def delete(self, locator: str) -> None:
        self._locator_path(locator).unlink(missing_ok=True)

    def exists(self, locator: str) -> bool:
        try:
            return self._locator_path(locator).is_file()
        except ObjectStorageError:
            return False

    @contextmanager
    def materialize(self, locator: str, suffix: str = "") -> Iterator[Path]:
        del suffix
        yield self._locator_path(locator)


class MinioObjectStore:
    def __init__(self, binding: TenantBinding) -> None:
        try:
            from minio import Minio
        except ImportError as exc:  # pragma: no cover - container dependency check
            raise ObjectStorageError("MinIO backend requires the minio Python package") from exc
        if not binding.object_endpoint or not binding.data_bucket:
            raise ObjectStorageError("MinIO endpoint and data bucket are required")
        self.binding = binding
        endpoint = binding.object_endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        self.client = Minio(
            endpoint,
            access_key=binding.object_access_key,
            secret_key=binding.object_secret_key,
            secure=binding.object_secure,
        )
        self.bucket = binding.data_bucket

    def _parse(self, locator: str) -> tuple[str, str]:
        parsed = urlparse(locator)
        if parsed.scheme != "s3" or parsed.netloc != self.bucket:
            raise ObjectStorageError("object does not belong to institution bucket")
        return parsed.netloc, safe_object_key(parsed.path.lstrip("/"))

    def put_bytes(self, object_key: str, content: bytes, content_type: str = "application/octet-stream") -> StoredObject:
        object_key = safe_object_key(object_key)
        sse = None
        if self.binding.kms_key_id:
            from minio.sse import SseKMS

            sse = SseKMS(self.binding.kms_key_id, {})
        self.client.put_object(
            self.bucket,
            object_key,
            BytesIO(content),
            length=len(content),
            content_type=content_type or "application/octet-stream",
            sse=sse,
        )
        return StoredObject(
            locator=f"s3://{self.bucket}/{object_key}",
            bucket=self.bucket,
            object_key=object_key,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            kms_key_id=self.binding.kms_key_id,
        )

    def put_file(
        self,
        object_key: str,
        source: Path,
        content_type: str = "application/octet-stream",
    ) -> StoredObject:
        source = source.resolve()
        if not source.is_file():
            raise ObjectStorageError("source file does not exist")
        object_key = safe_object_key(object_key)
        sse = None
        if self.binding.kms_key_id:
            from minio.sse import SseKMS

            sse = SseKMS(self.binding.kms_key_id, {})
        try:
            self.client.fput_object(
                self.bucket,
                object_key,
                str(source),
                content_type=content_type or "application/octet-stream",
                sse=sse,
            )
        except Exception as exc:
            raise ObjectStorageError(f"unable to upload object: {object_key}") from exc
        return StoredObject(
            locator=f"s3://{self.bucket}/{object_key}",
            bucket=self.bucket,
            object_key=object_key,
            size_bytes=source.stat().st_size,
            sha256=_file_sha256(source),
            kms_key_id=self.binding.kms_key_id,
        )

    def read_bytes(self, locator: str) -> bytes:
        bucket, object_key = self._parse(locator)
        try:
            response = self.client.get_object(bucket, object_key)
        except Exception as exc:
            raise ObjectStorageError(f"unable to read object: {object_key}") from exc
        try:
            return response.read()
        except Exception as exc:
            raise ObjectStorageError(f"unable to read object body: {object_key}") from exc
        finally:
            response.close()
            response.release_conn()

    def copy_to(self, locator: str, destination: Path) -> Path:
        bucket, object_key = self._parse(locator)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            self.client.fget_object(bucket, object_key, str(temporary))
            temporary.replace(destination)
            return destination
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise ObjectStorageError(f"unable to download object: {object_key}") from exc

    def delete(self, locator: str) -> None:
        bucket, object_key = self._parse(locator)
        try:
            self.client.remove_object(bucket, object_key)
        except Exception as exc:
            raise ObjectStorageError(f"unable to delete object: {object_key}") from exc

    def exists(self, locator: str) -> bool:
        try:
            from minio.error import S3Error

            bucket, object_key = self._parse(locator)
            self.client.stat_object(bucket, object_key)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket", "NotFound"}:
                return False
            raise ObjectStorageError("unable to query object storage") from exc
        except ObjectStorageError:
            raise
        except Exception as exc:
            raise ObjectStorageError("unable to query object storage") from exc

    @contextmanager
    def materialize(self, locator: str, suffix: str = "") -> Iterator[Path]:
        handle = tempfile.NamedTemporaryFile(prefix="longyun-object-", suffix=suffix, delete=False)
        path = Path(handle.name)
        try:
            handle.close()
            self.copy_to(locator, path)
            yield path
        finally:
            try:
                handle.close()
            finally:
                path.unlink(missing_ok=True)


class ObjectStorageManager:
    def __init__(self) -> None:
        self.local_root = Path(
            os.getenv("OBJECT_STORAGE_LOCAL_ROOT", os.getenv("RESEARCH_STORAGE_DIR", "./data/research"))
        ) / "institutions"
        self._stores: dict[str, ObjectStore] = {}
        self._lock = threading.RLock()

    def for_institution(self, institution_id: str) -> ObjectStore:
        institution_id = normalize_institution_id(institution_id)
        with self._lock:
            store = self._stores.get(institution_id)
            if store is not None:
                return store
            binding = tenant_database_manager.resolve(institution_id)
            if binding.storage_backend == "minio":
                store = MinioObjectStore(binding)
            elif binding.storage_backend == "local":
                legacy_roots: tuple[Path, ...] = ()
                if (
                    tenant_database_manager.mode == "single"
                    and institution_id == tenant_database_manager.default_institution_id
                ):
                    legacy_roots = (
                        Path(os.getenv("RESEARCH_STORAGE_DIR", "./data/research")),
                        Path(os.getenv("RAW_STORAGE_DIR", "./data/raw")),
                    )
                store = LocalObjectStore(institution_id, self.local_root, legacy_roots)
            else:
                raise ObjectStorageError(f"unsupported storage backend: {binding.storage_backend}")
            self._stores[institution_id] = store
            return store


object_storage_manager = ObjectStorageManager()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
