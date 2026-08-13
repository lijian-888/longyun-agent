"""Register the existing business schema as the migration baseline.

Revision ID: 0001_existing_schema
Revises: None
"""

from typing import Sequence, Union


revision: str = "0001_existing_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Historical tables are created by compatibility bootstrap functions before
    # this baseline is applied.  They are intentionally neither recreated nor
    # dropped, so existing institutional data remains untouched.
    pass


def downgrade() -> None:
    pass
