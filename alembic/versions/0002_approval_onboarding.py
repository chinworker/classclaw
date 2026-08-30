"""Add mandatory write proposals and class onboarding.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

from app.database import Base
from app.models import entities  # noqa: F401


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("class_subjects", "class_onboarding_sessions", "write_proposals"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("write_proposals", "class_onboarding_sessions", "class_subjects"):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
