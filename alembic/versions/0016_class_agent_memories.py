"""Class-scoped, confirmed agent memories.

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    if "class_agent_memories" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "class_agent_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("class_id", sa.String(36), sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=False),
        sa.Column("content", sa.String(400), nullable=False),
        sa.Column("start_time", sa.String(5)),
        sa.Column("end_time", sa.String(5)),
        sa.Column("weekdays_json", sa.JSON(), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("class_id", "memory_key", name="uq_class_agent_memory_key"),
    )
    op.create_index("ix_class_agent_memories_class_id", "class_agent_memories", ["class_id"])


def downgrade():
    op.drop_table("class_agent_memories")
