"""Audit and remove historical business fixtures while preserving the base showcase.

Run this with the migration/owner database credential.  The command is dry-run
by default and requires an exact confirmation phrase before it can delete.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection


BASE_PACKAGE_CODE = "RICE-MET-2023-2025-DEMO"
BASE_PROGRAM_CODE = "JX-RICE-DEMO-2021"
CONFIRMATION = "DELETE-LEGACY-DEMO-KEEP-BASE"
DEMO_SOURCE_PATTERN = r"(样例|示例|模拟|demo)"


def _database_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def _scalar(connection: Connection, sql: str, **parameters: Any) -> int:
    return int(connection.scalar(text(sql), parameters) or 0)


def _table_exists(connection: Connection, table_name: str) -> bool:
    return table_name in inspect(connection).get_table_names()


def audit(connection: Connection) -> dict[str, Any]:
    package = connection.execute(text("""
        SELECT package.id, package.package_code,
               COUNT(DISTINCT trial.id) AS trial_count,
               COUNT(DISTINCT entry.id) AS entry_count,
               COUNT(DISTINCT sample.id) AS sample_count,
               COUNT(DISTINCT observation.id) AS observation_count
        FROM trial_data_package package
        LEFT JOIN field_trial trial ON trial.package_id=package.id
        LEFT JOIN trial_entry entry ON entry.trial_id=trial.id
        LEFT JOIN biological_sample sample ON sample.trial_entry_id=entry.id
        LEFT JOIN field_survey_observation observation ON observation.sample_id=sample.id
        WHERE package.package_code=:package_code
        GROUP BY package.id, package.package_code
    """), {"package_code": BASE_PACKAGE_CODE}).mappings().first()
    return {
        "preserved_base_showcase": dict(package) if package else None,
        "delete_candidates": {
            "simulated_trial_packages": _scalar(connection, """
                SELECT COUNT(*) FROM trial_data_package
                WHERE is_simulated=TRUE AND package_code<>:package_code
            """, package_code=BASE_PACKAGE_CODE),
            "simulated_field_tasks": _scalar(connection, """
                SELECT COUNT(*) FROM field_survey_task WHERE is_simulated=TRUE
            """),
            "simulated_program_materials": _scalar(connection, """
                SELECT COUNT(*)
                FROM breeding_program_material link
                WHERE link.is_simulated=TRUE
                  AND NOT EXISTS (
                    SELECT 1 FROM biological_sample sample
                    WHERE sample.program_id=link.program_id
                      AND sample.material_id=link.material_id
                  )
            """),
            "simulated_pedigrees": _scalar(connection, """
                SELECT COUNT(*) FROM breeding_pedigree_relationship WHERE is_simulated=TRUE
            """),
            "simulated_generations": _scalar(connection, """
                SELECT COUNT(*) FROM breeding_generation_record WHERE is_simulated=TRUE
            """),
            "simulated_selections": _scalar(connection, """
                SELECT COUNT(*) FROM breeding_selection_record WHERE is_simulated=TRUE
            """),
            "legacy_demo_sources": _scalar(connection, """
                SELECT COUNT(*) FROM source_review WHERE source_name ~* :pattern
            """, pattern=DEMO_SOURCE_PATTERN),
            "legacy_demo_varieties": _scalar(connection, """
                SELECT COUNT(*)
                FROM variety_basic variety
                JOIN source_review source ON source.id=variety.source_review_id
                WHERE source.source_name ~* :pattern
            """, pattern=DEMO_SOURCE_PATTERN),
        },
    }


def clean(connection: Connection) -> dict[str, int]:
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('longyun-demo-cleanup'))"))
    connection.execute(text("""
        CREATE TEMP TABLE cleanup_extra_package ON COMMIT DROP AS
        SELECT id FROM trial_data_package
        WHERE is_simulated=TRUE AND package_code<>:package_code
    """), {"package_code": BASE_PACKAGE_CODE})
    sample_count = _scalar(connection, """
        SELECT COUNT(*)
        FROM biological_sample sample
        JOIN trial_entry entry ON entry.id=sample.trial_entry_id
        JOIN field_trial trial ON trial.id=entry.trial_id
        JOIN cleanup_extra_package package ON package.id=trial.package_id
    """)
    if sample_count:
        raise RuntimeError(
            "Refusing cleanup: a non-base simulated package owns biological samples. "
            "Review that package manually before deleting it."
        )

    counts: dict[str, int] = {}
    result = connection.execute(text("""
        DELETE FROM trial_import_batch batch
        WHERE batch.published_package_id IN (SELECT id FROM cleanup_extra_package)
           OR lower(batch.display_name)='rcbd-verification'
    """))
    counts["trial_import_batches"] = result.rowcount
    result = connection.execute(text("""
        DELETE FROM field_trial trial
        WHERE trial.package_id IN (SELECT id FROM cleanup_extra_package)
    """))
    counts["field_trials"] = result.rowcount
    result = connection.execute(text("""
        DELETE FROM trial_data_package package
        WHERE package.id IN (SELECT id FROM cleanup_extra_package)
    """))
    counts["trial_packages"] = result.rowcount

    # Keep any simulated survey task that is actually connected to the approved
    # showcase samples.  Historical standalone field-demo tasks are removed.
    result = connection.execute(text("""
        DELETE FROM field_survey_task task
        WHERE task.is_simulated=TRUE
          AND NOT EXISTS (
            SELECT 1
            FROM field_survey_observation observation
            JOIN biological_sample sample ON sample.id=observation.sample_id
            JOIN breeding_program program ON program.id=sample.program_id
            WHERE observation.task_id=task.id
              AND program.program_code=:program_code
          )
    """), {"program_code": BASE_PROGRAM_CODE})
    counts["field_survey_tasks"] = result.rowcount

    result = connection.execute(text("""
        DELETE FROM breeding_selection_record selection
        WHERE selection.is_simulated=TRUE
          AND NOT EXISTS (
            SELECT 1
            FROM biological_sample sample
            JOIN breeding_program program ON program.id=sample.program_id
            WHERE sample.id=selection.sample_id
              AND program.program_code=:program_code
          )
    """), {"program_code": BASE_PROGRAM_CODE})
    counts["breeding_selections"] = result.rowcount
    for table_name, key in (
        ("breeding_generation_record", "breeding_generations"),
        ("breeding_pedigree_relationship", "breeding_pedigrees"),
    ):
        result = connection.execute(text(f"DELETE FROM {table_name} WHERE is_simulated=TRUE"))
        counts[key] = result.rowcount
    result = connection.execute(text("""
        DELETE FROM breeding_program_material link
        WHERE link.is_simulated=TRUE
          AND NOT EXISTS (
            SELECT 1 FROM biological_sample sample
            WHERE sample.program_id=link.program_id
              AND sample.material_id=link.material_id
          )
    """))
    counts["breeding_program_materials"] = result.rowcount

    # The mock dossier created synthetic parent materials with these dedicated
    # codes.  Delete only rows no longer referenced by any business table.
    result = connection.execute(text("""
        DELETE FROM breeding_material material
        WHERE (material.material_code LIKE 'JX-CMS-A%' OR material.material_code LIKE 'JX-RF-R%')
          AND NOT EXISTS (SELECT 1 FROM trial_entry entry WHERE entry.material_id=material.id)
          AND NOT EXISTS (SELECT 1 FROM biological_sample sample WHERE sample.material_id=material.id)
          AND NOT EXISTS (SELECT 1 FROM genotype_sample_mapping mapping WHERE mapping.material_id=material.id)
          AND NOT EXISTS (
            SELECT 1 FROM breeding_pedigree_relationship relation
            WHERE relation.child_material_id=material.id OR relation.parent_material_id=material.id
          )
    """))
    counts["synthetic_parent_materials"] = result.rowcount

    connection.execute(text("""
        CREATE TEMP TABLE cleanup_demo_source ON COMMIT DROP AS
        SELECT id FROM source_review WHERE source_name ~* :pattern
    """), {"pattern": DEMO_SOURCE_PATTERN})
    connection.execute(text("""
        CREATE TEMP TABLE cleanup_demo_variety ON COMMIT DROP AS
        SELECT id FROM variety_basic
        WHERE source_review_id IN (SELECT id FROM cleanup_demo_source)
    """))
    if _table_exists(connection, "data_entity_lineage"):
        result = connection.execute(text("""
            DELETE FROM data_entity_lineage lineage
            WHERE lineage.entity_id IN (SELECT id FROM cleanup_demo_variety)
              AND lineage.entity_type='variety_basic'
        """))
        counts["demo_lineage"] = result.rowcount
    result = connection.execute(text("""
        DELETE FROM field_change_request
        WHERE source_review_id IN (SELECT id FROM cleanup_demo_source)
    """))
    counts["demo_change_requests"] = result.rowcount
    for table_name, key in (
        ("phenotype_observation", "demo_phenotypes"),
        ("root_phenotype_observation", "demo_root_phenotypes"),
    ):
        result = connection.execute(text(f"""
            DELETE FROM {table_name}
            WHERE source_review_id IN (SELECT id FROM cleanup_demo_source)
               OR variety_id IN (SELECT id FROM cleanup_demo_variety)
        """))
        counts[key] = result.rowcount
    result = connection.execute(text("""
        DELETE FROM variety_basic
        WHERE id IN (SELECT id FROM cleanup_demo_variety)
    """))
    counts["demo_varieties"] = result.rowcount
    result = connection.execute(text("""
        DELETE FROM source_review
        WHERE id IN (SELECT id FROM cleanup_demo_source)
    """))
    counts["demo_sources"] = result.rowcount
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.demo_data_admin")
    parser.add_argument(
        "--database-url",
        default=os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL"),
        help="Institution business database owner URL; defaults to MIGRATION_DATABASE_URL/DATABASE_URL.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply the audited cleanup transaction.")
    parser.add_argument("--confirm", default="", help=f"Required with --apply: {CONFIRMATION}")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or MIGRATION_DATABASE_URL/DATABASE_URL is required")
    if args.apply and args.confirm != CONFIRMATION:
        parser.error(f"--apply requires --confirm {CONFIRMATION}")

    engine = create_engine(_database_url(args.database_url), future=True)
    if not args.apply:
        with engine.connect() as connection:
            print(json.dumps(audit(connection), ensure_ascii=False, indent=2, default=str))
        return
    with engine.begin() as connection:
        before = audit(connection)
        if not before["preserved_base_showcase"]:
            raise RuntimeError(
                f"Approved base package {BASE_PACKAGE_CODE} is missing; refusing an ambiguous cleanup."
            )
        deleted = clean(connection)
        after = audit(connection)
    print(json.dumps({"before": before, "deleted": deleted, "after": after}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
