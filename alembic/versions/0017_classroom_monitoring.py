"""Classroom terminal, camera registry, broadcast ledger and media view leases.

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

def _timestamp_columns():
    # Column 对象只能归属一张表，每次建表必须创建新的实例。
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "classroom_devices" not in existing:
        op.create_table(
            "classroom_devices",
            sa.Column("id", sa.String(36), primary_key=True),
            *_timestamp_columns(),
            sa.Column("class_id", sa.String(36), sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("pairing_status", sa.String(20), nullable=False),
            sa.Column("pairing_code_hash", sa.String(64)),
            sa.Column("pairing_expires_at", sa.DateTime(timezone=True)),
            sa.Column("credential_hash", sa.String(64)),
            sa.Column("paired_at", sa.DateTime(timezone=True)),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
            sa.Column("protocol_version", sa.Integer(), nullable=False),
            sa.Column("app_version", sa.String(50)),
            sa.Column("os_version", sa.String(100)),
            sa.Column("capabilities_json", sa.JSON(), nullable=False),
            sa.Column("inventory_json", sa.JSON(), nullable=False),
            sa.Column("display_name", sa.String(200)),
            sa.Column("audio_output_name", sa.String(200)),
            sa.Column("volume_level", sa.Integer()),
            sa.Column("muted", sa.Boolean(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True)),
            sa.Column("last_error", sa.Text()),
            sa.Column("config_revision", sa.Integer(), nullable=False),
            sa.UniqueConstraint("class_id", name="uq_classroom_device_class"),
            sa.CheckConstraint("pairing_status IN ('unpaired', 'paired', 'revoked')", name="ck_classroom_device_pairing"),
        )
        op.create_index("ix_classroom_devices_class_id", "classroom_devices", ["class_id"])
    if "classroom_broadcasts" not in existing:
        op.create_table(
            "classroom_broadcasts",
            sa.Column("id", sa.String(36), primary_key=True),
            *_timestamp_columns(),
            sa.Column("class_id", sa.String(36), sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("mode", sa.String(20), nullable=False),
            sa.Column("merge_mode", sa.String(20), nullable=False),
            sa.Column("salutation", sa.String(60)),
            sa.Column("time_phrase", sa.String(60)),
            sa.Column("predicate", sa.String(200)),
            sa.Column("segments_json", sa.JSON(), nullable=False),
            sa.Column("display_seconds", sa.Integer(), nullable=False),
            sa.Column("repeat_count", sa.Integer(), nullable=False),
            sa.Column("gap_seconds", sa.Integer(), nullable=False),
            sa.Column("volume", sa.Integer()),
            sa.Column("target_screen", sa.String(200)),
            sa.Column("display_status", sa.String(20), nullable=False),
            sa.Column("speak_status", sa.String(20), nullable=False),
            sa.Column("created_by", sa.String(200)),
            sa.Column("source_type", sa.String(20), nullable=False),
            sa.Column("source_message_id", sa.String(200)),
            sa.Column("proposal_id", sa.String(36)),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint("mode IN ('three_part', 'custom')", name="ck_classroom_broadcast_mode"),
            sa.CheckConstraint("merge_mode IN ('combined', 'per_student')", name="ck_classroom_broadcast_merge"),
        )
        op.create_index("ix_classroom_broadcasts_class_id", "classroom_broadcasts", ["class_id"])
        op.create_index("ix_classroom_broadcast_class_created", "classroom_broadcasts", ["class_id", "created_at"])
    if "classroom_device_commands" not in existing:
        op.create_table(
            "classroom_device_commands",
            sa.Column("id", sa.String(36), primary_key=True),
            *_timestamp_columns(),
            sa.Column("class_id", sa.String(36), sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("device_id", sa.String(36), sa.ForeignKey("classroom_devices.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(40), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("broadcast_id", sa.String(36), sa.ForeignKey("classroom_broadcasts.id", ondelete="SET NULL")),
            sa.Column("idempotency_key", sa.String(200)),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True)),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("result_json", sa.JSON(), nullable=False),
            sa.Column("error_code", sa.String(100)),
            sa.Column("error_message", sa.Text()),
            sa.Column("requested_by", sa.String(200)),
            sa.Column("source_type", sa.String(20), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_classroom_command_idempotency"),
            sa.CheckConstraint(
                "status IN ('authorized', 'delivered', 'executing', 'succeeded', 'failed', 'expired', 'unknown')",
                name="ck_classroom_command_status",
            ),
        )
        op.create_index("ix_classroom_device_commands_class_id", "classroom_device_commands", ["class_id"])
        op.create_index("ix_classroom_device_commands_device_id", "classroom_device_commands", ["device_id"])
        op.create_index("ix_classroom_device_commands_broadcast_id", "classroom_device_commands", ["broadcast_id"])
        op.create_index("ix_classroom_command_pending", "classroom_device_commands", ["device_id", "status"])
    if "classroom_cameras" not in existing:
        op.create_table(
            "classroom_cameras",
            sa.Column("id", sa.String(36), primary_key=True),
            *_timestamp_columns(),
            sa.Column("class_id", sa.String(36), sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("access_path", sa.String(20), nullable=False),
            sa.Column("source_kind", sa.String(20), nullable=False),
            sa.Column("device_id", sa.String(36), sa.ForeignKey("classroom_devices.id", ondelete="SET NULL")),
            sa.Column("device_identifier", sa.String(200)),
            sa.Column("device_label", sa.String(200)),
            sa.Column("protocol", sa.String(20)),
            sa.Column("location", sa.String(500)),
            sa.Column("credential_ref", sa.String(100)),
            sa.Column("video_json", sa.JSON(), nullable=False),
            sa.Column("audio_capable", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("config_revision", sa.Integer(), nullable=False),
            sa.Column("connected_at", sa.DateTime(timezone=True)),
            sa.Column("disconnected_at", sa.DateTime(timezone=True)),
            sa.Column("last_error", sa.Text()),
            sa.Column("updated_by", sa.String(200)),
            sa.UniqueConstraint("class_id", name="uq_classroom_camera_class"),
            sa.CheckConstraint("config_revision >= 1", name="ck_classroom_camera_revision"),
            sa.CheckConstraint("access_path IN ('windows_capture', 'server_direct', 'windows_relay')", name="ck_classroom_camera_path"),
            sa.CheckConstraint("source_kind IN ('windows_device', 'network_stream')", name="ck_classroom_camera_source"),
        )
        op.create_index("ix_classroom_cameras_class_id", "classroom_cameras", ["class_id"])
        op.create_index("ix_classroom_cameras_device_id", "classroom_cameras", ["device_id"])
    if "classroom_media_sessions" not in existing:
        op.create_table(
            "classroom_media_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            *_timestamp_columns(),
            sa.Column("class_id", sa.String(36), sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("camera_id", sa.String(36), sa.ForeignKey("classroom_cameras.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("video_allowed", sa.Boolean(), nullable=False),
            sa.Column("audio_allowed", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("surface", sa.String(20), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True)),
            sa.Column("released_at", sa.DateTime(timezone=True)),
            sa.Column("revoke_reason", sa.String(60)),
            sa.Column("created_by", sa.String(200)),
            sa.UniqueConstraint("token_hash", name="uq_classroom_media_token"),
            sa.CheckConstraint("status IN ('active', 'released', 'revoked', 'expired')", name="ck_classroom_media_status"),
        )
        op.create_index("ix_classroom_media_sessions_class_id", "classroom_media_sessions", ["class_id"])
        op.create_index("ix_classroom_media_sessions_camera_id", "classroom_media_sessions", ["camera_id"])
        op.create_index("ix_classroom_media_sessions_user_id", "classroom_media_sessions", ["user_id"])
        op.create_index("ix_classroom_media_active", "classroom_media_sessions", ["camera_id", "status"])


def downgrade():
    op.drop_table("classroom_media_sessions")
    op.drop_table("classroom_cameras")
    op.drop_table("classroom_device_commands")
    op.drop_table("classroom_broadcasts")
    op.drop_table("classroom_devices")
