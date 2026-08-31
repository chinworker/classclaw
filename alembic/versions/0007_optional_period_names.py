"""Allow class periods without a custom name.

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("class_periods") as batch:
        batch.alter_column("name", existing_type=sa.String(length=100), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE class_periods SET name = '第' || period_no || '节' WHERE name IS NULL OR trim(name) = ''")
    with op.batch_alter_table("class_periods") as batch:
        batch.alter_column("name", existing_type=sa.String(length=100), nullable=False)
