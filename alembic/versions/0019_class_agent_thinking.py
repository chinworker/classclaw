"""Persist per-class Agent thinking defaults.

Revision ID: 0019
Revises: 0018
"""
import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("class_agent_bindings")}
    if "thinking_default" not in columns:
        op.add_column("class_agent_bindings", sa.Column("thinking_default", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("class_agent_bindings", "thinking_default")
