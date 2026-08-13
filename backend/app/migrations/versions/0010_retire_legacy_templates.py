"""Remove two obsolete first-generation phenotype import templates.

Revision ID: 0010_retire_legacy_templates
Revises: 0009_project_material_evidence
"""

from __future__ import annotations

import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "0010_retire_legacy_templates"
down_revision = "0009_project_material_evidence"
branch_labels = None
depends_on = None


RETIRED_TEMPLATE_CODES = ("rice_data_center", "rice_root_phenotype")


def upgrade() -> None:
    connection = op.get_bind()
    template_rows = connection.execute(sa.text("""
        SELECT id
        FROM data_template
        WHERE template_code IN ('rice_data_center', 'rice_root_phenotype')
    """)).mappings().all()
    template_ids = [str(row["id"]) for row in template_rows]
    if not template_ids:
        return

    version_ids = [
        str(value)
        for value in connection.execute(
            sa.text("SELECT id FROM template_version WHERE template_id IN :template_ids")
            .bindparams(sa.bindparam("template_ids", expanding=True)),
            {"template_ids": template_ids},
        ).scalars().all()
    ]
    if version_ids:
        # Historical source files and observations remain available.  Only the
        # obsolete processing-template reference is detached before deletion.
        connection.execute(
            sa.text("UPDATE source_review SET template_version_id=NULL WHERE template_version_id IN :version_ids")
            .bindparams(sa.bindparam("version_ids", expanding=True)),
            {"version_ids": version_ids},
        )
        connection.execute(
            sa.text("UPDATE data_import_batch SET template_version_id=NULL WHERE template_version_id IN :version_ids")
            .bindparams(sa.bindparam("version_ids", expanding=True)),
            {"version_ids": version_ids},
        )

    connection.execute(
        sa.text("DELETE FROM field_change_request WHERE template_id IN :template_ids")
        .bindparams(sa.bindparam("template_ids", expanding=True)),
        {"template_ids": template_ids},
    )
    connection.execute(
        sa.text("UPDATE data_template SET current_version_id=NULL WHERE id IN :template_ids")
        .bindparams(sa.bindparam("template_ids", expanding=True)),
        {"template_ids": template_ids},
    )
    connection.execute(
        sa.text("DELETE FROM template_version WHERE template_id IN :template_ids")
        .bindparams(sa.bindparam("template_ids", expanding=True)),
        {"template_ids": template_ids},
    )
    connection.execute(
        sa.text("DELETE FROM data_template WHERE id IN :template_ids")
        .bindparams(sa.bindparam("template_ids", expanding=True)),
        {"template_ids": template_ids},
    )


def downgrade() -> None:
    # A full release rollback also restores the previous seeding code.  These
    # minimal definitions make the database downgrade self-contained; the old
    # application can subsequently publish a richer template version as usual.
    connection = op.get_bind()
    definitions = (
        (
            "rice_data_center",
            "国家水稻数据中心信息标准",
            "水稻品种与地上部表型",
            "phenotype_observation",
            "用于国家水稻数据中心网页、审定资料及同类品种表型数据的归集。",
            "品种名称",
        ),
        (
            "rice_root_phenotype",
            "水稻根系表型数据标准",
            "水稻根系表型",
            "root_phenotype_observation",
            "用于根系扫描、根系成像和人工测量等根系表型数据。",
            "材料/品种名称",
        ),
    )
    for code, name, scope, target, description, name_label in definitions:
        if connection.execute(
            sa.text("SELECT 1 FROM data_template WHERE template_code=:code"),
            {"code": code},
        ).scalar():
            continue
        template_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        fields = json.dumps([{
            "code": "variety_name",
            "name": name_label,
            "category": "基础信息",
            "unit": "",
            "aliases": [name_label],
            "required": True,
            "kind": "basic",
        }], ensure_ascii=False)
        connection.execute(sa.text("""
            INSERT INTO data_template(
                id, template_code, template_name, data_scope, target_table,
                description, current_version_id, status, created_at
            ) VALUES (
                :id, :code, :name, :scope, :target, :description,
                NULL, 'published', CURRENT_TIMESTAMP
            )
        """), {
            "id": template_id,
            "code": code,
            "name": name,
            "scope": scope,
            "target": target,
            "description": description,
        })
        connection.execute(sa.text("""
            INSERT INTO template_version(
                id, template_id, version, change_summary, field_definitions,
                status, created_by, created_at
            ) VALUES (
                :id, :template_id, 'v1.0', '回滚恢复的基础模板',
                CAST(:fields AS json), 'published', '系统管理员', CURRENT_TIMESTAMP
            )
        """), {"id": version_id, "template_id": template_id, "fields": fields})
        connection.execute(sa.text("""
            UPDATE data_template SET current_version_id=:version_id WHERE id=:template_id
        """), {"version_id": version_id, "template_id": template_id})
