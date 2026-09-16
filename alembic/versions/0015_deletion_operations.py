"""Persist resumable deletion operations.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    if "deletion_operations" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "deletion_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("operator_id", sa.String(36)),
        sa.Column("owner_user_id", sa.String(36)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("phase", sa.String(30), nullable=False),
        sa.Column("resources_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.UniqueConstraint("target_type", "target_id", name="uq_deletion_target"),
    )


def downgrade():
    op.drop_table("deletion_operations")
