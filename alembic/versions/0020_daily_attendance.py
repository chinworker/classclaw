"""One attendance record per student and date.

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # Do not guess a daily status or discard history when old periods conflict.
    duplicate = connection.execute(sa.text(
        "SELECT 1 FROM attendance_records GROUP BY student_id, attendance_date HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError("存在同一学生同日的多条历史考勤；请先核对并合并为每天一条，迁移未修改这些记录")
    connection.execute(sa.text("UPDATE attendance_records SET period = 'full_day' WHERE period <> 'full_day'"))
    inspector = sa.inspect(connection)
    uniques = {item["name"] for item in inspector.get_unique_constraints("attendance_records")}
    checks = {item["name"] for item in inspector.get_check_constraints("attendance_records")}
    with op.batch_alter_table("attendance_records") as batch:
        if "uq_attendance_slot" in uniques:
            batch.drop_constraint("uq_attendance_slot", type_="unique")
        if "uq_attendance_day" not in uniques:
            batch.create_unique_constraint("uq_attendance_day", ["student_id", "attendance_date"])
        if "ck_attendance_daily_period" not in checks:
            batch.create_check_constraint("ck_attendance_daily_period", "period = 'full_day'")
    # An old preview of a half-day must not authorize a whole-day write after this migration.
    connection.execute(sa.text(
        "UPDATE write_proposals SET status = 'cancelled', error_message = '考勤已改为每日一次，请重新生成预览' "
        "WHERE operation_type = 'attendance.set' AND status = 'pending_review'"
    ))
    connection.execute(sa.text("DELETE FROM analysis_cache"))


def downgrade() -> None:
    with op.batch_alter_table("attendance_records") as batch:
        batch.drop_constraint("ck_attendance_daily_period", type_="check")
        batch.drop_constraint("uq_attendance_day", type_="unique")
        batch.create_unique_constraint("uq_attendance_slot", ["student_id", "attendance_date", "period"])
