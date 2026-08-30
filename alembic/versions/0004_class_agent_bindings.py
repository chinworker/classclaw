"""Add one isolated OpenClaw agent binding per class.

Revision ID: 0004
Revises: 0003
"""
from alembic import op

from app.database import Base
from app.models import entities  # noqa: F401


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["class_agent_bindings"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.tables["class_agent_bindings"].drop(bind=op.get_bind(), checkfirst=True)
