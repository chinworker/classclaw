"""Persist owner-scoped removal of Gateway-backed web conversations.

Revision ID: 0021
Revises: 0020
"""
from alembic import op
from app.models.entities import AgentChatHiddenConversation

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    AgentChatHiddenConversation.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    AgentChatHiddenConversation.__table__.drop(bind=op.get_bind(), checkfirst=True)
