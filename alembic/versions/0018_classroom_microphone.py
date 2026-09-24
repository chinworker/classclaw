"""Explicit classroom microphone selection.

Revision ID: 0018
Revises: 0017
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    if "microphone_identifier" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("classroom_cameras")}:
        op.add_column("classroom_cameras", sa.Column("microphone_identifier", sa.String(200), nullable=True))


def downgrade():
    op.drop_column("classroom_cameras", "microphone_identifier")
