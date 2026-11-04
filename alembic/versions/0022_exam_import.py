"""Preserve source ranks and totals from exam files.

Revision ID: 0022
Revises: 0021
"""
import sqlalchemy as sa

from alembic import op
from app.models.entities import ExamImportedTotal

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("scores")}
    for name in ("imported_class_rank", "imported_grade_rank"):
        if name not in columns:
            op.add_column("scores", sa.Column(name, sa.Integer(), nullable=True))
    ExamImportedTotal.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    ExamImportedTotal.__table__.drop(bind=op.get_bind())
    for name in ("imported_grade_rank", "imported_class_rank"):
        op.drop_column("scores", name)
