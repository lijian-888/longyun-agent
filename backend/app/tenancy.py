"""Tenant control-plane registry and institution database routing.

The authenticated ``institution_id`` is the only routing input accepted by
this module.  Browser supplied database names, bucket names, or queue names
are deliberately ignored.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator, Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


INSTITUTION_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{1,62}")
ACTIVE_TENANT_STATUSES = frozenset({"active", "trial"})


class TenantError(RuntimeError):
    """Base class for fail-closed tenant routing errors."""


class TenantAccessError(TenantError):
    """The requested institution is unknown, frozen, or destroyed."""


class TenantConfigurationError(TenantError):
    """An institution exists but its private resources are incomplete."""


def normalize_institution_id(value: str) -> str:
    institution_id = (value or "").strip().lower()
    if not INSTITUTION_ID_PATTERN.fullmatch(institution_id):
        raise TenantAccessError("invalid institution identity")
    return institution_id


def queue_namespace(institution_id: str) -> str:
    """Return a broker-safe queue namespace derived from verified identity."""
    return f"tenant.{normalize_institution_id(institution_id).replace('_', '-')}"


def workflow_queue_name(institution_id: str) -> str:
    return f"{queue_namespace(institution_id)}.agent"


@dataclass(frozen=True)
class TenantBinding:
    institution_id: str
    display_name: str
    status: str
    database_url: str
    migration_database_url: str
    storage_backend: str = "local"
    object_endpoint: str = ""
    data_bucket: str = ""
    backup_bucket: str = ""
    object_access_key: str = ""
    object_secret_key: str = ""
    object_secure: bool = False
    kms_key_id: str = ""
    queue_prefix: str = ""

    @property
    def workflow_queue(self) -> str:
        return f"{self.queue_prefix or queue_namespace(self.institution_id)}.agent"


CONTROL_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS institution (
        id VARCHAR(64) PRIMARY KEY,
        display_name VARCHAR(240) NOT NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'active',
        trial_started_at TIMESTAMPTZ,
        expires_at TIMESTAMPTZ,
        purge_at TIMESTAMPTZ,
        deployment_mode VARCHAR(30) NOT NULL DEFAULT 'shared',
        settings JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS institution_user (
        keycloak_user_id VARCHAR(120) PRIMARY KEY,
        institution_id VARCHAR(64) NOT NULL REFERENCES institution(id) ON DELETE CASCADE,
        status VARCHAR(30) NOT NULL DEFAULT 'active',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_resource_binding (
        institution_id VARCHAR(64) PRIMARY KEY REFERENCES institution(id) ON DELETE CASCADE,
        database_url_secret VARCHAR(160) NOT NULL,
        migration_database_url_secret VARCHAR(160) NOT NULL,
        storage_backend VARCHAR(20) NOT NULL DEFAULT 'local',
        object_endpoint VARCHAR(500),
        data_bucket VARCHAR(160),
        backup_bucket VARCHAR(160),
        object_access_key_secret VARCHAR(160),
        object_secret_key_secret VARCHAR(160),
        object_secure BOOLEAN NOT NULL DEFAULT false,
        kms_key_id VARCHAR(240),
        queue_prefix VARCHAR(160) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_lifecycle_job (
        id VARCHAR(36) PRIMARY KEY,
        institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
        job_type VARCHAR(40) NOT NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'pending',
        scheduled_at TIMESTAMPTZ NOT NULL,
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        error_detail TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_admin_audit (
        id VARCHAR(36) PRIMARY KEY,
        keycloak_user_id VARCHAR(120) NOT NULL,
        institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
        action VARCHAR(40) NOT NULL,
        operator_id VARCHAR(120) NOT NULL,
        reason TEXT NOT NULL,
        identity_provider_status VARCHAR(30) NOT NULL DEFAULT 'not_requested',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_institution_status ON institution(status)",
    "CREATE INDEX IF NOT EXISTS ix_lifecycle_due ON tenant_lifecycle_job(status, scheduled_at)",
    "CREATE INDEX IF NOT EXISTS ix_account_admin_audit_user ON account_admin_audit(keycloak_user_id, created_at DESC)",
)


class TenantDatabaseManager:
    """Resolve tenant bindings and cache one SQLAlchemy pool per institution."""

    def __init__(self) -> None:
        self.mode = os.getenv("TENANCY_MODE", "single").strip().lower()
        if self.mode not in {"single", "multi"}:
            raise TenantConfigurationError("TENANCY_MODE must be single or multi")
        self.default_institution_id = normalize_institution_id(
            os.getenv("DEFAULT_INSTITUTION_ID", "longyun-demo")
        )
        self.default_database_url = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://rice:rice_demo_password@localhost:54329/rice_demo",
        )
        self.default_migration_url = os.getenv(
            "MIGRATION_DATABASE_URL", self.default_database_url
        )
        self.control_database_url = os.getenv("CONTROL_DATABASE_URL", "").strip()
        self.cache_seconds = max(5, int(os.getenv("TENANT_BINDING_CACHE_SECONDS", "30")))
        self.pool_size = max(1, int(os.getenv("TENANT_DB_POOL_SIZE", "8")))
        self.max_overflow = max(0, int(os.getenv("TENANT_DB_MAX_OVERFLOW", "8")))
        self._control_engine: Engine | None = None
        self._engines: dict[tuple[str, bool], Engine] = {}
        self._sessionmakers: dict[str, sessionmaker] = {}
        self._binding_cache: dict[str, tuple[float, TenantBinding]] = {}
        self._lock = threading.RLock()

    def _engine(self, url: str) -> Engine:
        return create_engine(
            url,
            pool_pre_ping=True,
            pool_size=self.pool_size,
            max_overflow=self.max_overflow,
            pool_recycle=1800,
        )

    @property
    def control_engine(self) -> Engine:
        if self.mode != "multi":
            raise TenantConfigurationError("control plane is disabled in single-tenant mode")
        if not self.control_database_url:
            raise TenantConfigurationError("CONTROL_DATABASE_URL is required in multi-tenant mode")
        with self._lock:
            if self._control_engine is None:
                self._control_engine = self._engine(self.control_database_url)
            return self._control_engine

    def ensure_control_schema(self) -> None:
        if self.mode != "multi":
            return
        with self.control_engine.begin() as connection:
            for statement in CONTROL_SCHEMA_STATEMENTS:
                connection.execute(text(statement))
        self._bootstrap_from_environment()

    def _bootstrap_from_environment(self) -> None:
        payload = os.getenv("TENANT_BOOTSTRAP_JSON", "[]").strip() or "[]"
        try:
            entries = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise TenantConfigurationError("TENANT_BOOTSTRAP_JSON is invalid JSON") from exc
        if not isinstance(entries, list):
            raise TenantConfigurationError("TENANT_BOOTSTRAP_JSON must contain a list")
        with self.control_engine.begin() as connection:
            for entry in entries:
                if not isinstance(entry, dict):
                    raise TenantConfigurationError("tenant bootstrap entries must be objects")
                institution_id = normalize_institution_id(str(entry.get("institution_id") or ""))
                display_name = str(entry.get("display_name") or institution_id).strip()
                queue_prefix = str(entry.get("queue_prefix") or queue_namespace(institution_id)).strip()
                connection.execute(text(
                    """
                    INSERT INTO institution(id, display_name, status, deployment_mode)
                    VALUES (:id, :display_name, :status, :deployment_mode)
                    ON CONFLICT (id) DO UPDATE SET
                        display_name=excluded.display_name,
                        deployment_mode=excluded.deployment_mode,
                        updated_at=now()
                    """
                ), {
                    "id": institution_id,
                    "display_name": display_name,
                    "status": str(entry.get("status") or "active"),
                    "deployment_mode": str(entry.get("deployment_mode") or "shared"),
                })
                connection.execute(text(
                    """
                    INSERT INTO tenant_resource_binding(
                        institution_id, database_url_secret, migration_database_url_secret,
                        storage_backend, object_endpoint, data_bucket, backup_bucket,
                        object_access_key_secret, object_secret_key_secret, object_secure,
                        kms_key_id, queue_prefix
                    ) VALUES (
                        :institution_id, :database_url_secret, :migration_database_url_secret,
                        :storage_backend, :object_endpoint, :data_bucket, :backup_bucket,
                        :object_access_key_secret, :object_secret_key_secret, :object_secure,
                        :kms_key_id, :queue_prefix
                    )
                    ON CONFLICT (institution_id) DO UPDATE SET
                        database_url_secret=excluded.database_url_secret,
                        migration_database_url_secret=excluded.migration_database_url_secret,
                        storage_backend=excluded.storage_backend,
                        object_endpoint=excluded.object_endpoint,
                        data_bucket=excluded.data_bucket,
                        backup_bucket=excluded.backup_bucket,
                        object_access_key_secret=excluded.object_access_key_secret,
                        object_secret_key_secret=excluded.object_secret_key_secret,
                        object_secure=excluded.object_secure,
                        kms_key_id=excluded.kms_key_id,
                        queue_prefix=excluded.queue_prefix,
                        updated_at=now()
                    """
                ), {
                    "institution_id": institution_id,
                    "database_url_secret": str(entry.get("database_url_secret") or ""),
                    "migration_database_url_secret": str(
                        entry.get("migration_database_url_secret")
                        or entry.get("database_url_secret")
                        or ""
                    ),
                    "storage_backend": str(entry.get("storage_backend") or "local"),
                    "object_endpoint": str(entry.get("object_endpoint") or ""),
                    "data_bucket": str(entry.get("data_bucket") or ""),
                    "backup_bucket": str(entry.get("backup_bucket") or ""),
                    "object_access_key_secret": str(entry.get("object_access_key_secret") or ""),
                    "object_secret_key_secret": str(entry.get("object_secret_key_secret") or ""),
                    "object_secure": bool(entry.get("object_secure", False)),
                    "kms_key_id": str(entry.get("kms_key_id") or ""),
                    "queue_prefix": queue_prefix,
                })
                user_ids = entry.get("user_ids") or []
                if not isinstance(user_ids, list):
                    raise TenantConfigurationError("tenant user_ids must contain a list")
                for user_id in user_ids:
                    normalized_user_id = str(user_id or "").strip()
                    if not normalized_user_id:
                        raise TenantConfigurationError("tenant user_ids cannot contain empty values")
                    existing_institution = connection.execute(text(
                        "SELECT institution_id FROM institution_user WHERE keycloak_user_id=:user_id"
                    ), {"user_id": normalized_user_id}).scalar_one_or_none()
                    if existing_institution and existing_institution != institution_id:
                        raise TenantConfigurationError(
                            f"Keycloak user {normalized_user_id} is already bound to {existing_institution}"
                        )
                    connection.execute(text(
                        """
                        INSERT INTO institution_user(keycloak_user_id, institution_id, status)
                        VALUES (:user_id, :institution_id, 'active')
                        ON CONFLICT (keycloak_user_id) DO UPDATE SET
                            status='active',
                            updated_at=now()
                        """
                    ), {
                        "user_id": normalized_user_id,
                        "institution_id": institution_id,
                    })
        self.clear_binding_cache()

    @staticmethod
    def _resolve_secret(name: str, *, required: bool = True) -> str:
        value = os.getenv((name or "").strip(), "").strip()
        if required and not value:
            raise TenantConfigurationError(f"tenant secret environment variable is missing: {name}")
        return value

    def _single_binding(self, institution_id: str) -> TenantBinding:
        if institution_id != self.default_institution_id:
            raise TenantAccessError("institution is not registered for this deployment")
        return TenantBinding(
            institution_id=institution_id,
            display_name=institution_id,
            status="active",
            database_url=self.default_database_url,
            migration_database_url=self.default_migration_url,
            storage_backend=os.getenv("OBJECT_STORAGE_BACKEND", "local").strip().lower(),
            object_endpoint=os.getenv("MINIO_ENDPOINT", "").strip(),
            data_bucket=os.getenv("MINIO_BUCKET", "").strip(),
            backup_bucket=os.getenv("MINIO_BACKUP_BUCKET", "").strip(),
            object_access_key=os.getenv("MINIO_ACCESS_KEY", "").strip(),
            object_secret_key=os.getenv("MINIO_SECRET_KEY", "").strip(),
            object_secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
            kms_key_id=os.getenv("MINIO_KMS_KEY_ID", "").strip(),
            queue_prefix=queue_namespace(institution_id),
        )

    def resolve(self, institution_id: str, *, allow_inactive: bool = False) -> TenantBinding:
        institution_id = normalize_institution_id(institution_id)
        if self.mode == "single":
            return self._single_binding(institution_id)
        now = time.monotonic()
        cached = self._binding_cache.get(institution_id)
        if cached and cached[0] > now:
            binding = cached[1]
        else:
            with self.control_engine.connect() as connection:
                row = connection.execute(text(
                    """
                    SELECT i.id, i.display_name,
                           CASE
                             WHEN i.status='trial' AND i.expires_at IS NOT NULL AND i.expires_at <= now()
                             THEN 'expired'
                             ELSE i.status
                           END AS status,
                           b.database_url_secret, b.migration_database_url_secret,
                           b.storage_backend, b.object_endpoint, b.data_bucket,
                           b.backup_bucket, b.object_access_key_secret,
                           b.object_secret_key_secret, b.object_secure,
                           b.kms_key_id, b.queue_prefix
                    FROM institution i
                    JOIN tenant_resource_binding b ON b.institution_id = i.id
                    WHERE i.id = :institution_id
                    """
                ), {"institution_id": institution_id}).mappings().one_or_none()
            if not row:
                raise TenantAccessError("institution is not registered")
            storage_backend = str(row["storage_backend"] or "local").lower()
            binding = TenantBinding(
                institution_id=institution_id,
                display_name=str(row["display_name"]),
                status=str(row["status"]),
                database_url=self._resolve_secret(str(row["database_url_secret"])),
                migration_database_url=self._resolve_secret(str(row["migration_database_url_secret"])),
                storage_backend=storage_backend,
                object_endpoint=str(row["object_endpoint"] or ""),
                data_bucket=str(row["data_bucket"] or ""),
                backup_bucket=str(row["backup_bucket"] or ""),
                object_access_key=self._resolve_secret(
                    str(row["object_access_key_secret"] or ""),
                    required=storage_backend == "minio",
                ),
                object_secret_key=self._resolve_secret(
                    str(row["object_secret_key_secret"] or ""),
                    required=storage_backend == "minio",
                ),
                object_secure=bool(row["object_secure"]),
                kms_key_id=str(row["kms_key_id"] or ""),
                queue_prefix=str(row["queue_prefix"] or queue_namespace(institution_id)),
            )
            self._binding_cache[institution_id] = (now + self.cache_seconds, binding)
        if not allow_inactive and binding.status not in ACTIVE_TENANT_STATUSES:
            raise TenantAccessError(f"institution is {binding.status}")
        return binding

    def active_bindings(self) -> list[TenantBinding]:
        if self.mode == "single":
            return [self._single_binding(self.default_institution_id)]
        with self.control_engine.connect() as connection:
            ids = connection.execute(text(
                "SELECT id FROM institution WHERE status IN ('active', 'trial') ORDER BY id"
            )).scalars().all()
        return [self.resolve(str(item)) for item in ids]

    def verify_user_membership(self, keycloak_user_id: str, institution_id: str) -> None:
        """Optionally enforce the one-Keycloak-subject/one-institution invariant."""
        if self.mode != "multi":
            return
        if os.getenv("CONTROL_ENFORCE_USER_BINDING", "true").lower() != "true":
            return
        institution_id = normalize_institution_id(institution_id)
        with self.control_engine.connect() as connection:
            row = connection.execute(text(
                """
                SELECT institution_id, status
                FROM institution_user
                WHERE keycloak_user_id = :user_id
                """
            ), {"user_id": (keycloak_user_id or "").strip()}).mappings().one_or_none()
        if not row or row["status"] != "active" or row["institution_id"] != institution_id:
            raise TenantAccessError("account is not bound to this institution")

    def clear_binding_cache(self) -> None:
        with self._lock:
            self._binding_cache.clear()

    def engine_for(self, institution_id: str, *, migration: bool = False) -> Engine:
        binding = self.resolve(institution_id)
        key = (binding.institution_id, migration)
        with self._lock:
            engine = self._engines.get(key)
            if engine is None:
                engine = self._engine(
                    binding.migration_database_url if migration else binding.database_url
                )
                self._engines[key] = engine
            return engine

    def session_factory(self, institution_id: str) -> sessionmaker:
        institution_id = normalize_institution_id(institution_id)
        with self._lock:
            factory = self._sessionmakers.get(institution_id)
            if factory is None:
                factory = sessionmaker(
                    bind=self.engine_for(institution_id),
                    autoflush=False,
                    autocommit=False,
                    expire_on_commit=False,
                )
                self._sessionmakers[institution_id] = factory
            return factory

    @contextmanager
    def session(self, institution_id: str) -> Iterator[Session]:
        session = self.session_factory(institution_id)()
        try:
            yield session
        finally:
            session.close()

    @contextmanager
    def migration_session(self, institution_id: str) -> Iterator[Session]:
        factory = sessionmaker(
            bind=self.engine_for(institution_id, migration=True),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def verify_tenant_database(self, institution_id: str) -> None:
        """Detect a bad control-plane DSN before it can leak another tenant."""
        institution_id = normalize_institution_id(institution_id)
        with self.engine_for(institution_id, migration=True).begin() as connection:
            connection.execute(text(
                """
                CREATE TABLE IF NOT EXISTS tenant_metadata (
                    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
                    institution_id VARCHAR(64) NOT NULL UNIQUE,
                    schema_version VARCHAR(40) NOT NULL DEFAULT 'legacy-compatible',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            ))
            existing = connection.execute(text(
                "SELECT institution_id FROM tenant_metadata WHERE singleton = true"
            )).scalar_one_or_none()
            if existing and existing != institution_id:
                raise TenantConfigurationError(
                    f"database belongs to {existing}, not {institution_id}"
                )
            if not existing:
                connection.execute(text(
                    "INSERT INTO tenant_metadata(singleton, institution_id) VALUES (true, :id)"
                ), {"id": institution_id})


tenant_database_manager = TenantDatabaseManager()
