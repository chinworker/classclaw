"""Add explicit dynamic and static duty schedule metadata.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("duty_schedules")}


def upgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("duty_schedules") as batch:
        if "schedule_type" not in columns:
            batch.add_column(sa.Column("schedule_type", sa.String(length=20), nullable=False, server_default="static"))
        if "situation" not in columns:
            batch.add_column(sa.Column("situation", sa.Text(), nullable=True))


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("duty_schedules") as batch:
        if "situation" in columns:
            batch.drop_column("situation")
        if "schedule_type" in columns:
            batch.drop_column("schedule_type")
