"""Add seating snapshot names and direct duty scores.

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "name" not in _columns("seating_snapshots"):
        with op.batch_alter_table("seating_snapshots") as batch:
            batch.add_column(sa.Column("name", sa.String(length=100), nullable=True))
    op.execute(
        "UPDATE seating_snapshots "
        "SET name = '座位表 ' || substr(snapshot_at, 1, 16) "
        "WHERE name IS NULL OR trim(name) = ''"
    )
    with op.batch_alter_table("seating_snapshots") as batch:
        batch.alter_column("name", existing_type=sa.String(length=100), nullable=False)

    if "score" not in _columns("duty_assignments"):
        with op.batch_alter_table("duty_assignments") as batch:
            batch.add_column(sa.Column("score", sa.Float(), nullable=True))


def downgrade() -> None:
    if "score" in _columns("duty_assignments"):
        with op.batch_alter_table("duty_assignments") as batch:
            batch.drop_column("score")
    if "name" in _columns("seating_snapshots"):
        with op.batch_alter_table("seating_snapshots") as batch:
            batch.drop_column("name")
