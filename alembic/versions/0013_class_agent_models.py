"""Add per-class Agent model preferences.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("class_agent_bindings")}


def upgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("class_agent_bindings") as batch:
        for name in ("main_model", "image_model", "speech_model"):
            if name not in columns:
                batch.add_column(sa.Column(name, sa.String(length=200), nullable=True))


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("class_agent_bindings") as batch:
        for name in ("speech_model", "image_model", "main_model"):
            if name in columns:
                batch.drop_column(name)
