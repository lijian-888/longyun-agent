"""HTTP boundary for project-scoped institutional data intake."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import CurrentUser, require_data_processor, require_field_admin, require_published_data_reader
from .data_spine import (
    DATA_DOMAIN_LABELS,
    FEATURE_REQUIREMENTS,
    assess_project_readiness,
    create_import_batch,
    get_import_batch,
    list_import_batches,
    register_import_file,
    serialize_record,
    transition_import_batch,
)
from .object_storage import ObjectStorageError, ObjectStorageManager, project_object_key
from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .real_data_intake import (
    IntakeError,
    create_extension_field,
    list_batch_issues,
    list_semantic_fields,
    parse_structured_file,
    publish_staging_batch,
    save_batch_mapping,
    stage_parsed_tables,
    validate_staging_batch,
)
from .tenancy import TenantAccessError, tenant_database_manager


@dataclass(frozen=True)
class DataSpineApiDependencies:
    get_session: Callable[..., Session]
    object_storage_manager: ObjectStorageManager


class ImportBatchCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=300)
    data_domain: Literal[
        "germplasm",
        "pedigree",
        "phenotype",
        "environment",
        "management",
        "genotype",
        "trial",
        "literature",
        "mixed",
    ]
    template_version_id: str | None = Field(default=None, max_length=36)
    notes: str | None = Field(default=None, max_length=4000)


class ImportBatchTransition(BaseModel):
    status: Literal["uploading", "validating", "ready", "published", "failed", "cancelled"]
    row_count: int | None = Field(default=None, ge=0)
    accepted_count: int | None = Field(default=None, ge=0)
    rejected_count: int | None = Field(default=None, ge=0)
    warning_count: int | None = Field(default=None, ge=0)
    summary: dict[str, Any] | None = None


class ResearchProjectCreate(BaseModel):
    project_name: str = Field(min_length=2, max_length=240)
    research_direction: str | None = Field(default=None, max_length=240)


class ProjectMembershipUpsert(BaseModel):
    user_id: str = Field(min_length=1, max_length=120)


class SemanticFieldCreate(BaseModel):
    field_code: str = Field(min_length=1, max_length=180)
    data_domain: Literal[
        "germplasm", "pedigree", "phenotype", "environment", "management",
        "genotype", "trial", "literature", "mixed",
    ]
    target_entity: str = Field(min_length=1, max_length=100)
    target_field: str = Field(min_length=1, max_length=160)
    field_name: str = Field(min_length=1, max_length=240)
    value_type: Literal["text", "integer", "number", "number_or_text", "text_list", "date", "datetime"] = "text"
    unit: str | None = Field(default=None, max_length=60)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class FieldMappingItem(BaseModel):
    source_column: str = Field(min_length=1, max_length=300)
    semantic_field_id: str | None = Field(default=None, max_length=36)
    mapping_action: Literal["map", "preserve", "ignore"] = "map"
    transform_rule: dict[str, Any] = Field(default_factory=dict)


class BatchMappingSave(BaseModel):
    profile_name: str = Field(min_length=1, max_length=240)
    mappings: list[FieldMappingItem] = Field(min_length=1, max_length=1000)
    binding_context: dict[str, Any] = Field(default_factory=dict)


DOMAIN_UPLOAD_LIMITS: dict[str, int] = {
    "germplasm": 250 * 1024 * 1024,
    "pedigree": 250 * 1024 * 1024,
    "phenotype": 500 * 1024 * 1024,
    "environment": 500 * 1024 * 1024,
    "management": 250 * 1024 * 1024,
    "genotype": 5 * 1024 * 1024 * 1024,
    "trial": 500 * 1024 * 1024,
    "literature": 500 * 1024 * 1024,
    "mixed": 2 * 1024 * 1024 * 1024,
}

DOMAIN_FILE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "germplasm": (".csv", ".tsv", ".xlsx", ".xls", ".json"),
    "pedigree": (".csv", ".tsv", ".xlsx", ".xls", ".json"),
    "phenotype": (".csv", ".tsv", ".xlsx", ".xls", ".json"),
    "environment": (".csv", ".tsv", ".xlsx", ".xls", ".json", ".nc"),
    "management": (".csv", ".tsv", ".xlsx", ".xls", ".json"),
    "genotype": (
        ".vcf",
        ".vcf.gz",
        ".bcf",
        ".bed",
        ".bim",
        ".fam",
        ".pgen",
        ".pvar",
        ".psam",
        ".csv",
        ".tsv",
        ".xlsx",
        ".zip",
    ),
    "trial": (".csv", ".tsv", ".xlsx", ".xls", ".json", ".zip"),
    "literature": (".pdf", ".docx", ".txt", ".md", ".html", ".htm", ".csv", ".xlsx", ".zip"),
    "mixed": (".zip", ".tar.gz", ".tgz"),
}

SOURCE_ROLE_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,79}")


def assert_project_access(session: Session, user: CurrentUser, project_id: str) -> None:
    project = session.execute(
        text(
            """
            SELECT id FROM research_project
            WHERE id=:project_id AND institution_id=:institution_id AND status='active'
            """
        ),
        {"project_id": project_id, "institution_id": user.institution_id},
    ).first()
    if not project:
        raise HTTPException(404, "未找到当前机构内的有效课题。")
    session.info["project_id"] = project_id
    session.execute(
        text("SELECT set_config('app.project_id', :project_id, true)"),
        {"project_id": project_id},
    )
    if {"data_processor", "field_admin"}.intersection(user.roles):
        return
    membership = session.execute(
        text(
            """
            SELECT 1 FROM project_membership
            WHERE project_id=:project_id AND institution_id=:institution_id
              AND user_id=:user_id
            """
        ),
        {
            "project_id": project_id,
            "institution_id": user.institution_id,
            "user_id": user.id,
        },
    ).first()
    if not membership:
        raise HTTPException(403, "当前账号不是该课题成员，不能访问该课题数据。")


def _validate_upload_name(data_domain: str, file_name: str) -> None:
    normalized = (file_name or "").strip().lower()
    if not normalized or not any(normalized.endswith(suffix) for suffix in DOMAIN_FILE_SUFFIXES[data_domain]):
        allowed = "、".join(DOMAIN_FILE_SUFFIXES[data_domain])
        raise HTTPException(422, f"{DATA_DOMAIN_LABELS[data_domain]}仅支持：{allowed}")


async def _spool_upload(file: UploadFile, max_bytes: int) -> tuple[Path, int]:
    handle = tempfile.NamedTemporaryFile(prefix="longyun-intake-", suffix=".upload", delete=False)
    path = Path(handle.name)
    size = 0
    try:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(413, f"文件超过当前数据类型允许的 {max_bytes // (1024 * 1024)} MB 上限。")
            handle.write(chunk)
        handle.flush()
        handle.close()
        if size == 0:
            raise HTTPException(422, "不能上传空文件。")
        return path, size
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def _catalog() -> dict[str, Any]:
    return {
        "domains": [
            {
                "code": code,
                "name": name,
                "allowed_suffixes": list(DOMAIN_FILE_SUFFIXES[code]),
                "max_file_bytes": DOMAIN_UPLOAD_LIMITS[code],
            }
            for code, name in DATA_DOMAIN_LABELS.items()
        ],
        "features": [
            {
                "feature_code": item.code,
                "feature_name": item.name,
                "required_domains": list(item.required),
                "recommended_domains": list(item.recommended),
            }
            for item in FEATURE_REQUIREMENTS.values()
        ],
        "policy": {
            "fields_are_institution_configurable": True,
            "published_batches_only_enable_features": True,
            "raw_object_locator_never_accepted_from_browser": True,
        },
    }


def build_data_spine_router(dependencies: DataSpineApiDependencies) -> APIRouter:
    router = APIRouter(prefix="/api/data-spine", tags=["data-spine"])

    @router.get("/projects")
    def projects(
        user: CurrentUser = Depends(require_published_data_reader),
        session: Session = Depends(dependencies.get_session),
    ) -> list[dict[str, Any]]:
        if {"data_processor", "field_admin"}.intersection(user.roles):
            rows = session.execute(
                text(
                    """
                    SELECT id, project_name, research_direction, status, created_at
                    FROM research_project
                    WHERE institution_id=:institution_id AND status='active'
                    ORDER BY created_at, project_name
                    """
                ),
                {"institution_id": user.institution_id},
            ).mappings().all()
        else:
            rows = session.execute(
                text(
                    """
                    SELECT project.id, project.project_name,
                           project.research_direction, project.status,
                           project.created_at
                    FROM research_project project
                    JOIN project_membership membership
                      ON membership.project_id=project.id
                     AND membership.institution_id=project.institution_id
                    WHERE project.institution_id=:institution_id
                      AND membership.user_id=:user_id
                      AND project.status='active'
                    ORDER BY project.created_at, project.project_name
                    """
                ),
                {"institution_id": user.institution_id, "user_id": user.id},
            ).mappings().all()
        return serialize_record([dict(row) for row in rows])

    @router.post("/projects", status_code=201)
    def create_project(
        payload: ResearchProjectCreate,
        user: CurrentUser = Depends(require_field_admin),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        existing = session.execute(
            text(
                """
                SELECT id FROM research_project
                WHERE institution_id=:institution_id
                  AND lower(project_name)=lower(:project_name)
                  AND status='active'
                """
            ),
            {
                "institution_id": user.institution_id,
                "project_name": payload.project_name.strip(),
            },
        ).first()
        if existing:
            raise HTTPException(409, "当前机构已经存在同名有效课题。")
        project_id = str(uuid4())
        row = session.execute(
            text(
                """
                INSERT INTO research_project(
                    id, institution_id, project_name,
                    research_direction, status, created_by
                ) VALUES (
                    :id, :institution_id, :project_name,
                    :research_direction, 'active', :created_by
                ) RETURNING id, project_name, research_direction, status, created_at
                """
            ),
            {
                "id": project_id,
                "institution_id": user.institution_id,
                "project_name": payload.project_name.strip(),
                "research_direction": (payload.research_direction or "").strip() or None,
                "created_by": user.id,
            },
        ).mappings().one()
        session.execute(text("""
            INSERT INTO project_membership(
                project_id, institution_id, user_id, project_role
            ) VALUES (
                :project_id, :institution_id, :user_id, 'field_admin'
            ) ON CONFLICT (project_id, user_id) DO NOTHING
        """), {
            "project_id": project_id,
            "institution_id": user.institution_id,
            "user_id": user.id,
        })
        session.commit()
        return serialize_record(dict(row))

    @router.get("/projects/{project_id}/members")
    def project_members(
        project_id: str,
        user: CurrentUser = Depends(require_field_admin),
        session: Session = Depends(dependencies.get_session),
    ) -> list[dict[str, Any]]:
        assert_project_access(session, user, project_id)
        rows = session.execute(text("""
            SELECT project_id, user_id, project_role, created_at
            FROM project_membership
            WHERE project_id=:project_id AND institution_id=:institution_id
            ORDER BY created_at, user_id
        """), {
            "project_id": project_id,
            "institution_id": user.institution_id,
        }).mappings().all()
        return serialize_record([dict(row) for row in rows])

    @router.get("/project-membership-overview")
    def project_membership_overview(
        user: CurrentUser = Depends(require_field_admin),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        """Give an institution admin one authoritative project/account responsibility view."""
        project_rows = session.execute(text("""
            SELECT id, project_name, research_direction, status, created_at
            FROM research_project
            WHERE institution_id=:institution_id AND status='active'
            ORDER BY created_at, project_name
        """), {"institution_id": user.institution_id}).mappings().all()
        membership_rows = session.execute(text("""
            SELECT membership.project_id, membership.user_id,
                   membership.project_role, membership.created_at
            FROM project_membership membership
            JOIN research_project project ON project.id=membership.project_id
            WHERE membership.institution_id=:institution_id
              AND project.institution_id=:institution_id
              AND project.status='active'
            ORDER BY membership.created_at, membership.user_id
        """), {"institution_id": user.institution_id}).mappings().all()

        directory_status = "available"
        try:
            accounts = KeycloakAdminClient().institution_users(user.institution_id)
        except KeycloakAdminError:
            # Project authorization remains usable when the identity admin API is temporarily
            # unavailable. Stable Keycloak subject IDs are still returned without leaking errors.
            directory_status = "unavailable"
            accounts = []
        account_by_id = {item["user_id"]: item for item in accounts}
        members_by_project: dict[str, list[dict[str, Any]]] = {}
        assigned_user_ids: set[str] = set()
        for row in membership_rows:
            item = dict(row)
            assigned_user_ids.add(item["user_id"])
            account = account_by_id.get(item["user_id"], {})
            item.update({
                "username": account.get("username") or "",
                "display_name": account.get("display_name") or item["user_id"],
                "platform_roles": account.get("platform_roles") or [],
                "account_enabled": account.get("enabled"),
            })
            members_by_project.setdefault(item["project_id"], []).append(item)

        projects = []
        for row in project_rows:
            item = dict(row)
            item["members"] = members_by_project.get(item["id"], [])
            item["member_count"] = len(item["members"])
            projects.append(item)
        return serialize_record({
            "projects": projects,
            "accounts": accounts,
            "unassigned_accounts": [
                item for item in accounts if item["user_id"] not in assigned_user_ids
            ],
            "identity_directory_status": directory_status,
        })

    @router.put("/projects/{project_id}/members")
    def upsert_project_member(
        project_id: str,
        payload: ProjectMembershipUpsert,
        user: CurrentUser = Depends(require_field_admin),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        assert_project_access(session, user, project_id)
        member_id = payload.user_id.strip()
        try:
            institution_accounts = KeycloakAdminClient().institution_users(user.institution_id)
        except KeycloakAdminError as exc:
            raise HTTPException(503, "机构账号目录暂时不可用，请稍后重试。") from exc
        account = next((item for item in institution_accounts if item["user_id"] == member_id), None)
        if not account:
            raise HTTPException(422, "该账号不属于当前机构，不能加入课题。")
        if not account.get("enabled"):
            raise HTTPException(422, "该账号已被禁用，不能加入课题。")
        platform_roles = [
            role for role in account.get("platform_roles", [])
            if role in {"data_processor", "field_admin", "researcher"}
        ]
        if not platform_roles:
            raise HTTPException(422, "该账号未配置数据处理员、字段管理员或科研人员角色。")
        primary_role = next(
            (role for role in ("researcher", "data_processor", "field_admin") if role in platform_roles),
            platform_roles[0],
        )
        try:
            tenant_database_manager.verify_user_membership(member_id, user.institution_id)
        except TenantAccessError as exc:
            raise HTTPException(422, "该账号尚未绑定当前机构，不能加入课题。") from exc
        row = session.execute(text("""
            INSERT INTO project_membership(
                project_id, institution_id, user_id, project_role
            ) VALUES (
                :project_id, :institution_id, :user_id, :project_role
            )
            ON CONFLICT (project_id, user_id) DO UPDATE SET
                project_role=EXCLUDED.project_role
            RETURNING project_id, user_id, project_role, created_at
        """), {
            "project_id": project_id,
            "institution_id": user.institution_id,
            "user_id": member_id,
            "project_role": primary_role,
        }).mappings().one()
        session.commit()
        result = dict(row)
        result.update({
            "username": account.get("username") or "",
            "display_name": account.get("display_name") or account.get("username") or member_id,
            "platform_roles": platform_roles,
            "account_enabled": True,
        })
        return serialize_record(result)

    @router.delete("/projects/{project_id}/members/{member_user_id}")
    def remove_project_member(
        project_id: str,
        member_user_id: str,
        user: CurrentUser = Depends(require_field_admin),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, bool]:
        assert_project_access(session, user, project_id)
        deleted = session.execute(text("""
            DELETE FROM project_membership
            WHERE project_id=:project_id AND institution_id=:institution_id
              AND user_id=:user_id
            RETURNING user_id
        """), {
            "project_id": project_id,
            "institution_id": user.institution_id,
            "user_id": member_user_id,
        }).first()
        if not deleted:
            raise HTTPException(404, "该账号不在当前课题中。")
        session.commit()
        return {"deleted": True}

    @router.get("/catalog")
    def catalog(
        _user: CurrentUser = Depends(require_published_data_reader),
    ) -> dict[str, Any]:
        return _catalog()

    @router.get("/semantic-fields")
    def semantic_fields(
        data_domain: str | None = Query(default=None),
        _user: CurrentUser = Depends(require_data_processor),
        session: Session = Depends(dependencies.get_session),
    ) -> list[dict[str, Any]]:
        if data_domain and data_domain not in DATA_DOMAIN_LABELS:
            raise HTTPException(422, "不支持的数据类型。")
        return serialize_record(list_semantic_fields(session, data_domain))

    @router.post("/semantic-fields", status_code=201)
    def add_semantic_field(
        payload: SemanticFieldCreate,
        user: CurrentUser = Depends(require_field_admin),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        try:
            row = create_extension_field(
                session,
                field_code=payload.field_code,
                data_domain=payload.data_domain,
                target_entity=payload.target_entity,
                target_field=payload.target_field,
                field_name=payload.field_name,
                value_type=payload.value_type,
                unit=payload.unit,
                aliases=payload.aliases,
                description=payload.description,
                created_by=user.id,
            )
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(409, "字段代码已存在。") from exc
        except IntakeError as exc:
            session.rollback()
            raise HTTPException(422, str(exc)) from exc
        return serialize_record(row)

    @router.get("/projects/{project_id}/readiness")
    def readiness(
        project_id: str,
        user: CurrentUser = Depends(require_published_data_reader),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        assert_project_access(session, user, project_id)
        return {
            "project_id": project_id,
            "features": assess_project_readiness(session, project_id),
        }

    @router.post("/projects/{project_id}/import-batches", status_code=201)
    def create_batch(
        project_id: str,
        payload: ImportBatchCreate,
        user: CurrentUser = Depends(require_data_processor),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        assert_project_access(session, user, project_id)
        try:
            row = create_import_batch(
                session,
                project_id=project_id,
                display_name=payload.display_name,
                data_domain=payload.data_domain,
                template_version_id=payload.template_version_id,
                notes=payload.notes,
                created_by=user.id,
            )
        except IntegrityError as exc:
            session.rollback()
            if payload.template_version_id:
                raise HTTPException(422, "模板版本不存在或不适用于当前导入。") from exc
            raise
        return serialize_record(row)

    @router.get("/projects/{project_id}/import-batches")
    def batches(
        project_id: str,
        data_domain: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=200),
        user: CurrentUser = Depends(require_data_processor),
        session: Session = Depends(dependencies.get_session),
    ) -> list[dict[str, Any]]:
        assert_project_access(session, user, project_id)
        try:
            rows = list_import_batches(
                session,
                project_id=project_id,
                data_domain=data_domain,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return serialize_record(rows)

    @router.post("/import-batches/{batch_id}/files", status_code=201)
    async def upload_batch_file(
        batch_id: str,
        file: UploadFile = File(...),
        source_role: str = Query(default="primary"),
        user: CurrentUser = Depends(require_data_processor),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        if not SOURCE_ROLE_PATTERN.fullmatch(source_role):
            raise HTTPException(422, "source_role 只能使用小写字母、数字、下划线和连字符。")
        batch = get_import_batch(session, batch_id)
        if not batch:
            raise HTTPException(404, "导入批次不存在。")
        assert_project_access(session, user, batch["project_id"])
        _validate_upload_name(batch["data_domain"], file.filename or "")
        # Do not hold a database transaction while a multi-gigabyte file is
        # streamed to institution storage. register_import_file re-locks and
        # revalidates the batch state before recording the asset.
        session.rollback()
        max_bytes = min(
            DOMAIN_UPLOAD_LIMITS[batch["data_domain"]],
            int(os.getenv("DATA_SPINE_ABSOLUTE_UPLOAD_MAX_BYTES", str(5 * 1024 * 1024 * 1024))),
        )
        temporary, _size = await _spool_upload(file, max_bytes)
        try:
            parsed_tables = parse_structured_file(temporary, file.filename or "upload.bin")
        except IntakeError as exc:
            temporary.unlink(missing_ok=True)
            raise HTTPException(422, str(exc)) from exc
        store = dependencies.object_storage_manager.for_institution(user.institution_id)
        file_asset_id = str(uuid4())
        object_key = project_object_key(
            project_id=batch["project_id"],
            category=f"data-intake-{batch['data_domain']}",
            resource_id=file_asset_id,
            file_name=file.filename or "upload.bin",
        )
        stored = None
        try:
            stored = store.put_file(
                object_key,
                temporary,
                file.content_type or "application/octet-stream",
            )
            binding = tenant_database_manager.resolve(user.institution_id)
            row = register_import_file(
                session,
                batch_id=batch_id,
                owner_id=user.id,
                original_file_name=file.filename or "upload.bin",
                content_type=file.content_type or "application/octet-stream",
                source_role=source_role,
                storage_backend=binding.storage_backend,
                stored=stored,
            )
            profile = (
                stage_parsed_tables(
                    session,
                    import_batch_id=batch_id,
                    import_file_id=str(row["id"]),
                    tables=parsed_tables,
                )
                if parsed_tables
                else {
                    "structured": False,
                    "columns": [],
                    "row_count": 0,
                    "sample_rows": [],
                    "message": "文件已安全保存；该专用格式由对应业务适配器解析。",
                }
            )
            row["profile"] = profile
            return serialize_record(row)
        except (LookupError, ValueError) as exc:
            session.rollback()
            if stored is not None:
                store.delete(stored.locator)
            raise HTTPException(409, str(exc)) from exc
        except ObjectStorageError as exc:
            session.rollback()
            raise HTTPException(503, "机构文件存储暂不可用，导入批次未写入文件记录。") from exc
        except Exception:
            session.rollback()
            if stored is not None:
                store.delete(stored.locator)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    @router.get("/import-batches/{batch_id}/profile")
    def batch_profile(
        batch_id: str,
        user: CurrentUser = Depends(require_data_processor),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        batch = get_import_batch(session, batch_id)
        if not batch:
            raise HTTPException(404, "导入批次不存在。")
        assert_project_access(session, user, batch["project_id"])
        files = [dict(row) for row in session.execute(text(
            """
            SELECT file.id, file.source_role, file.sheet_name, file.parse_status,
                   file.detected_columns, file.row_count, asset.original_file_name,
                   asset.size_bytes, asset.sha256
            FROM data_import_file file
            JOIN data_file_asset asset ON asset.id=file.file_asset_id
            WHERE file.import_batch_id=:batch_id ORDER BY file.created_at
            """
        ), {"batch_id": batch_id}).mappings().all()]
        samples = [dict(row) for row in session.execute(text(
            """
            SELECT import_file_id, source_sheet, source_row_number, raw_record
            FROM data_import_staging_row WHERE import_batch_id=:batch_id
            ORDER BY import_file_id, source_row_number LIMIT 40
            """
        ), {"batch_id": batch_id}).mappings().all()]
        return serialize_record({
            "batch": batch,
            "files": files,
            "sample_rows": samples,
            "mapping_required": bool(samples) and not bool(batch.get("mapping_profile_id")),
        })

    @router.put("/import-batches/{batch_id}/mapping")
    def save_mapping(
        batch_id: str,
        payload: BatchMappingSave,
        user: CurrentUser = Depends(require_data_processor),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        batch = get_import_batch(session, batch_id)
        if not batch:
            raise HTTPException(404, "导入批次不存在。")
        assert_project_access(session, user, batch["project_id"])
        try:
            return serialize_record(save_batch_mapping(
                session,
                batch_id=batch_id,
                profile_name=payload.profile_name,
                mappings=[item.model_dump() for item in payload.mappings],
                binding_context=payload.binding_context,
                actor_id=user.id,
            ))
        except IntakeError as exc:
            session.rollback()
            raise HTTPException(422, str(exc)) from exc

    @router.post("/import-batches/{batch_id}/validate")
    def validate_batch(
        batch_id: str,
        user: CurrentUser = Depends(require_data_processor),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        batch = get_import_batch(session, batch_id)
        if not batch:
            raise HTTPException(404, "导入批次不存在。")
        assert_project_access(session, user, batch["project_id"])
        try:
            return serialize_record(validate_staging_batch(session, batch_id=batch_id, actor_id=user.id))
        except IntakeError as exc:
            session.rollback()
            raise HTTPException(422, str(exc)) from exc

    @router.get("/import-batches/{batch_id}/issues")
    def batch_issues(
        batch_id: str,
        limit: int = Query(default=500, ge=1, le=2000),
        user: CurrentUser = Depends(require_data_processor),
        session: Session = Depends(dependencies.get_session),
    ) -> list[dict[str, Any]]:
        batch = get_import_batch(session, batch_id)
        if not batch:
            raise HTTPException(404, "导入批次不存在。")
        assert_project_access(session, user, batch["project_id"])
        return serialize_record(list_batch_issues(session, batch_id, limit))

    @router.post("/import-batches/{batch_id}/publish")
    def publish_batch(
        batch_id: str,
        user: CurrentUser = Depends(require_data_processor),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        batch = get_import_batch(session, batch_id)
        if not batch:
            raise HTTPException(404, "导入批次不存在。")
        assert_project_access(session, user, batch["project_id"])
        try:
            return serialize_record(publish_staging_batch(session, batch_id=batch_id, actor_id=user.id))
        except IntakeError as exc:
            session.rollback()
            raise HTTPException(422, str(exc)) from exc

    @router.patch("/import-batches/{batch_id}")
    def transition_batch(
        batch_id: str,
        payload: ImportBatchTransition,
        user: CurrentUser = Depends(require_data_processor),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        batch = get_import_batch(session, batch_id)
        if not batch:
            raise HTTPException(404, "导入批次不存在。")
        assert_project_access(session, user, batch["project_id"])
        try:
            row = transition_import_batch(
                session,
                batch_id=batch_id,
                target_status=payload.status,
                row_count=payload.row_count,
                accepted_count=payload.accepted_count,
                rejected_count=payload.rejected_count,
                warning_count=payload.warning_count,
                summary=payload.summary,
            )
        except (LookupError, ValueError) as exc:
            session.rollback()
            raise HTTPException(409, str(exc)) from exc
        return serialize_record(row)

    return router
