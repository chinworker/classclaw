"""Remove message evidence storage and redundant indexes.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


INDEXES_TO_DROP = {
    "arrangements": ["ix_arrangements_priority", "ix_arrangements_source_message_id", "ix_arrangements_status"],
    "attachment_links": ["ix_attachment_links_entity_id", "ix_attachment_links_entity_type"],
    "attachments": ["ix_attachments_source_message_id"],
    "attendance_records": ["ix_attendance_records_attendance_date", "ix_attendance_records_period", "ix_attendance_records_source_message_id", "ix_attendance_records_status"],
    "audit_logs": ["ix_audit_logs_action", "ix_audit_logs_created_at", "ix_audit_logs_entity_type", "ix_audit_logs_source_message_id"],
    "base_timetable": ["ix_base_timetable_weekday"],
    "class_agent_bindings": ["ix_class_agent_bindings_class_id", "ix_class_agent_bindings_status"],
    "class_onboarding_sessions": ["ix_class_onboarding_sessions_source_message_id", "ix_class_onboarding_sessions_status"],
    "classes": ["ix_classes_status"],
    "duty_assignments": ["ix_duty_assignments_duty_date", "ix_duty_assignments_status"],
    "duty_rules": ["ix_duty_rules_status"],
    "duty_schedules": ["ix_duty_schedules_status"],
    "exams": ["ix_exams_exam_date", "ix_exams_status"],
    "homework": ["ix_homework_assigned_date", "ix_homework_due_at", "ix_homework_source_message_id", "ix_homework_status", "ix_homework_subject"],
    "homework_student_statuses": ["ix_homework_student_statuses_status"],
    "interaction_analyses": ["ix_interaction_analyses_channel", "ix_interaction_analyses_external_message_id", "ix_interaction_analyses_input_kind", "ix_interaction_analyses_intent", "ix_interaction_analyses_status"],
    "reminders": ["ix_reminders_status"],
    "scores": ["ix_scores_subject"],
    "seating_snapshots": ["ix_seating_snapshots_class_id", "ix_seating_snapshots_snapshot_at"],
    "student_events": ["ix_student_events_event_date", "ix_student_events_event_type", "ix_student_events_sentiment", "ix_student_events_severity", "ix_student_events_source_message_id", "ix_student_events_subject", "ix_student_events_subtype"],
    "students": ["ix_students_group_no", "ix_students_name", "ix_students_status"],
    "user_sessions": ["ix_user_sessions_expires_at", "ix_user_sessions_token_hash", "ix_user_sessions_user_id"],
    "users": ["ix_users_is_active", "ix_users_role", "ix_users_username"],
    "write_proposals": ["ix_write_proposals_expires_at", "ix_write_proposals_operation_type", "ix_write_proposals_source_message_id", "ix_write_proposals_status"],
}


def _drop_columns_if_present(table: str, names: list[str]) -> None:
    bind = op.get_bind()
    present = {column["name"] for column in sa.inspect(bind).get_columns(table)}
    targets = [name for name in names if name in present]
    if not targets:
        return
    with op.batch_alter_table(table) as batch:
        for name in targets:
            batch.drop_column(name)


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "_alembic_tmp_interaction_analyses" in tables:
        op.drop_table("_alembic_tmp_interaction_analyses")
        tables.remove("_alembic_tmp_interaction_analyses")
    if "interaction_analyses" in tables:
        existing = {index["name"] for index in sa.inspect(bind).get_indexes("interaction_analyses")}
        if "ix_interaction_analyses_message_id" in existing:
            op.drop_index("ix_interaction_analyses_message_id", table_name="interaction_analyses")
        _drop_columns_if_present("interaction_analyses", ["message_id", "raw_text"])
    if "write_proposals" in tables:
        existing = {index["name"] for index in sa.inspect(bind).get_indexes("write_proposals")}
        if "ix_write_proposals_payload_hash" in existing:
            op.drop_index("ix_write_proposals_payload_hash", table_name="write_proposals")
        _drop_columns_if_present("write_proposals", ["evidence_json", "payload_hash"])
    if "user_sessions" in tables:
        _drop_columns_if_present("user_sessions", ["last_seen_at"])

    for table in ("pending_actions", "messages"):
        if table in set(sa.inspect(bind).get_table_names()):
            op.drop_table(table)

    for table, names in INDEXES_TO_DROP.items():
        if table not in set(sa.inspect(bind).get_table_names()):
            continue
        existing = {index["name"] for index in sa.inspect(bind).get_indexes(table)}
        for name in names:
            if name in existing:
                op.drop_index(name, table_name=table)

    if "attachment_links" in set(sa.inspect(bind).get_table_names()):
        existing = {index["name"] for index in sa.inspect(bind).get_indexes("attachment_links")}
        if "ix_attachment_link_entity" not in existing:
            op.create_index("ix_attachment_link_entity", "attachment_links", ["entity_type", "entity_id"])


def downgrade() -> None:
    raise RuntimeError("0006 removes obsolete message evidence data and is intentionally irreversible")
