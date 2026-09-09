"""Move AI usage records to the independently connected usage database.

Revision ID: 0014
Revises: 0013
"""

from alembic import op
from app.config import settings
from app.database import build_engine
from app.models.usage import UsageBase
from app.usage_database import copy_verified, migrate_legacy_usage

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _usage_engine():
    url = op.get_context().config.attributes.get("usage_database_url", settings.usage_database_url)
    return build_engine(url)


def upgrade() -> None:
    engine = _usage_engine()
    try:
        migrate_legacy_usage(op.get_bind(), engine)
    finally:
        engine.dispose()


def downgrade() -> None:
    # Leave the independent copy intact; a later upgrade deduplicates by UUID.
    engine = _usage_engine()
    try:
        core = op.get_bind()
        UsageBase.metadata.create_all(core)
        with engine.connect() as source:
            copy_verified(source, core)
    finally:
        engine.dispose()
