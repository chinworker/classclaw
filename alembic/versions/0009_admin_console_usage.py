"""Add AI token usage records for the administrator console.

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "ai_usage_records" in inspector.get_table_names():
        return
    op.create_table(
        "ai_usage_records",
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("response_id", sa.String(length=200), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_usage_records_operation", "ai_usage_records", ["operation"])
    op.create_index("ix_ai_usage_created_operation", "ai_usage_records", ["created_at", "operation"])


def downgrade() -> None:
    if "ai_usage_records" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("ai_usage_records")
