"""Add administrator, head-teacher accounts and one-class ownership.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

from app.database import Base
from app.models import entities  # noqa: F401


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["users"].create(bind=bind, checkfirst=True)
    Base.metadata.tables["user_sessions"].create(bind=bind, checkfirst=True)
    user_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("users")}
    if "uq_users_single_admin" not in user_indexes:
        op.create_index("uq_users_single_admin", "users", ["role"], unique=True, sqlite_where=sa.text("role = 'admin'"))
    class_columns = {column["name"] for column in sa.inspect(bind).get_columns("classes")}
    if "owner_user_id" not in class_columns:
        # SQLite cannot add a foreign-key constraint to a populated table, and
        # rebuilding classes is blocked by its many existing child tables.
        # The service validates ownership; fresh databases still get the FK
        # from the current SQLAlchemy metadata created by revision 0001.
        op.add_column("classes", sa.Column("owner_user_id", sa.String(length=36), nullable=True))
        op.create_index("ix_classes_owner_user_id", "classes", ["owner_user_id"], unique=True)
    onboarding_columns = {column["name"] for column in sa.inspect(bind).get_columns("class_onboarding_sessions")}
    if "owner_user_id" not in onboarding_columns:
        op.add_column("class_onboarding_sessions", sa.Column("owner_user_id", sa.String(length=36), nullable=True))
        op.create_index("ix_class_onboarding_sessions_owner_user_id", "class_onboarding_sessions", ["owner_user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("class_onboarding_sessions") as batch:
        batch.drop_index("ix_class_onboarding_sessions_owner_user_id")
        batch.drop_column("owner_user_id")
    with op.batch_alter_table("classes") as batch:
        batch.drop_index("ix_classes_owner_user_id")
        batch.drop_column("owner_user_id")
    op.drop_table("user_sessions")
    op.drop_table("users")
