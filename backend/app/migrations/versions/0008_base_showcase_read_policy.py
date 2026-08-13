"""Keep the approved base showcase readable without weakening project writes.

Revision ID: 0008_base_showcase_read_policy
Revises: 0007_strict_project_partitions
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_base_showcase_read_policy"
down_revision = "0007_strict_project_partitions"
branch_labels = None
depends_on = None


BASE_SHOWCASE_PROGRAM_CODE = "JX-RICE-DEMO-2021"


def upgrade() -> None:
    # The base screen is a product showcase, not institution business data.
    # Give it a read-only exception while the existing FOR ALL policies remain
    # the sole authority for INSERT/UPDATE/DELETE.
    op.execute(sa.text("DROP POLICY IF EXISTS base_showcase_read ON breeding_program"))
    op.execute(sa.text(f"""
        CREATE POLICY base_showcase_read ON breeding_program FOR SELECT
        USING (
            project_id IS NULL
            AND program_code='{BASE_SHOWCASE_PROGRAM_CODE}'
            AND is_simulated=TRUE
        )
    """))
    op.execute(sa.text("DROP POLICY IF EXISTS base_showcase_read ON biological_sample"))
    op.execute(sa.text(f"""
        CREATE POLICY base_showcase_read ON biological_sample FOR SELECT
        USING (
            project_id IS NULL
            AND EXISTS (
                SELECT 1 FROM breeding_program program
                WHERE program.id=biological_sample.program_id
                  AND program.program_code='{BASE_SHOWCASE_PROGRAM_CODE}'
                  AND program.is_simulated=TRUE
            )
        )
    """))

    for table_name in (
        "field_survey_observation",
        "field_survey_photo",
        "breeding_selection_record",
    ):
        op.execute(sa.text(f"DROP POLICY IF EXISTS base_showcase_read ON {table_name}"))
        op.execute(sa.text(f"""
            CREATE POLICY base_showcase_read ON {table_name} FOR SELECT
            USING (
                sample_id IS NOT NULL
                AND EXISTS (
                    SELECT 1
                    FROM biological_sample sample
                    JOIN breeding_program program ON program.id=sample.program_id
                    WHERE sample.id={table_name}.sample_id
                      AND sample.project_id IS NULL
                      AND program.program_code='{BASE_SHOWCASE_PROGRAM_CODE}'
                      AND program.is_simulated=TRUE
                )
            )
        """))


def downgrade() -> None:
    for table_name in (
        "field_survey_observation",
        "field_survey_photo",
        "breeding_selection_record",
        "biological_sample",
        "breeding_program",
    ):
        op.execute(sa.text(f"DROP POLICY IF EXISTS base_showcase_read ON {table_name}"))
