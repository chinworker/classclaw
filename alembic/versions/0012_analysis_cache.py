"""Add analysis cache table for deterministic statistics.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

from app.database import Base
from app.models import entities  # noqa: F401

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["analysis_cache"].create(bind=bind, checkfirst=True)
    for index in Base.metadata.tables["analysis_cache"].indexes:
        index.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for index in Base.metadata.tables["analysis_cache"].indexes:
        index.drop(bind=bind, checkfirst=True)
    Base.metadata.tables["analysis_cache"].drop(bind=bind, checkfirst=True)
