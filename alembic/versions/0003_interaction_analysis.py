"""Add auditable OpenClaw interaction analyses.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

from app.database import Base
from app.models import entities  # noqa: F401


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["interaction_analyses"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.tables["interaction_analyses"].drop(bind=op.get_bind(), checkfirst=True)
