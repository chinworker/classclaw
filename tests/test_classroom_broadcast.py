"""点名广播：三段式与自定义句子的组合、冻结与执行结果。"""
from __future__ import annotations

from sqlalchemy import select

from app.models.entities import Arrangement, AttendanceRecord, ClassroomBroadcast, DutyAssignment, Student
from app.schemas.classroom import BroadcastComposeRequest
from app.services import classroom_broadcast
from tests.helpers import online_terminal, pair_terminal


def _send(client, class_id, body):
    response = client.post(f"/api/v1/classes/{class_id}/classroom/broadcasts", json=body)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _preview(client, class_id, body):
    response = client.post(f"/api/v1/classes/{class_id}/classroom/broadcasts/preview", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_three_part_single_student_builds_one_complete_sentence(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        data = _send(client, cls.id, {"mode": "three_part", "student_ids": [students[0].id],
                                      "time_phrase": "现在", "predicate": "去扫地"})
    assert data["texts"] == ["请张三同学现在去扫地。"]
    assert data["segments"][0]["recipients"] == [{"student_no": "001", "name": "张三"}]
    assert data["mode"] == "three_part" and data["merge_mode"] == "combined"


def test_multi_select_combined_and_per_student(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    ids = [students[1].id, students[0].id]
    with online_terminal(db, paired["device_id"], cls.id):
        combined = _send(client, cls.id, {"mode": "three_part", "student_ids": ids,
                                          "time_phrase": "下课后", "predicate": "来老师办公室"})
        per_student = _send(client, cls.id, {"mode": "three_part", "student_ids": ids, "merge_mode": "per_student",
                                             "time_phrase": "现在", "predicate": "去扫地"})
    assert combined["texts"] == ["请张三、李四同学下课后来老师办公室。"]
    # 冻结顺序按学号自然升序，不按传入顺序。
    assert per_student["texts"] == ["请张三同学现在去扫地。", "请李四同学现在去扫地。"]
    assert [row["student_no"] for row in per_student["segments"][0]["recipients"]] == ["001"]


def test_student_order_is_natural_not_lexicographic(client, db, sample):
    cls, _, _ = sample
    db.add_all([Student(class_id=cls.id, student_no="10", name="周十", tags=[], status="active"),
                Student(class_id=cls.id, student_no="2", name="钱二", tags=[], status="active")])
    db.commit()
    rows = list(db.scalars(select(Student).where(Student.class_id == cls.id, Student.student_no.in_(["10", "2"]))))
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        data = _send(client, cls.id, {"mode": "three_part", "student_ids": [row.id for row in rows],
                                      "merge_mode": "per_student", "predicate": "到讲台领取作业"})
    assert data["texts"] == ["请钱二同学现在到讲台领取作业。", "请周十同学现在到讲台领取作业。"]


def test_custom_sentence_is_not_wrapped_in_the_template(client, db, sample):
    cls, _, _ = sample
    text = "请今天负责卫生的同学现在带好工具到教室后门集合。"
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        data = _send(client, cls.id, {"mode": "custom", "text": text})
    assert data["texts"] == [text]
    assert data["segments"][0]["recipients"] == []
    assert data["salutation"] is None and data["predicate"] is None


def test_custom_sentence_rejects_students_and_empty_text(client, db, sample):
    cls, _, students = sample
    pair_terminal(client, cls)
    empty = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts/preview",
                        json={"mode": "custom", "text": "   "})
    assert empty.status_code == 422
    with_students = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts/preview",
                                json={"mode": "custom", "text": "上课", "student_ids": [students[0].id]})
    assert with_students.status_code == 422
    missing_predicate = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts/preview",
                                    json={"mode": "three_part", "student_ids": [students[0].id]})
    assert missing_predicate.status_code == 422


def test_control_characters_are_stripped_and_length_is_capped(client, db, sample):
    cls, _, _ = sample
    pair_terminal(client, cls)
    cleaned = _preview(client, cls.id, {"mode": "custom", "text": "  请\u0007同学\u001f们安静 "})
    assert cleaned["texts"] == ["请同学们安静"]

    long_text = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts/preview",
                            json={"mode": "custom", "text": "长" * 200})
    assert long_text.status_code == 422
    assert long_text.json()["error"]["code"] == "BROADCAST_TOO_LONG"

    too_many = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts/preview",
                           json={"mode": "three_part", "merge_mode": "per_student", "predicate": "去扫地",
                                 "student_ids": [cls.id]})
    assert too_many.status_code == 404
    assert too_many.json()["error"]["code"] == "STUDENT_NOT_FOUND"


def test_preview_is_read_only_and_send_freezes_the_same_text(client, db, sample):
    cls, _, students = sample
    body = {"mode": "three_part", "student_ids": [students[2].id], "time_phrase": "今天大课间", "predicate": "来老师办公室"}
    unpaired = _preview(client, cls.id, body)
    assert db.scalar(select(ClassroomBroadcast)) is None
    assert unpaired["device_online"] is False
    assert unpaired["warnings"] == ["本班尚未配对教室终端"]

    paired = pair_terminal(client, cls)
    offline = _preview(client, cls.id, body)
    assert offline["device_online"] is False and offline["warnings"] == []
    assert db.scalar(select(ClassroomBroadcast)) is None

    with online_terminal(db, paired["device_id"], cls.id):
        preview = _preview(client, cls.id, body)
        assert preview["device_online"] is True
        data = _send(client, cls.id, body)
    assert data["texts"] == preview["texts"] == unpaired["texts"] == ["请王五同学今天大课间来老师办公室。"]
    row = db.scalar(select(ClassroomBroadcast))
    assert [segment["text"] for segment in row.segments_json] == preview["texts"]


def test_broadcast_never_writes_attendance_duty_or_arrangements(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        _send(client, cls.id, {"mode": "three_part", "student_ids": [student.id for student in students[:3]],
                               "predicate": "去扫地"})
    assert db.scalar(select(AttendanceRecord)) is None
    assert db.scalar(select(DutyAssignment)) is None
    assert db.scalar(select(Arrangement)) is None


def test_stop_and_clear_are_separate_actions(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        data = _send(client, cls.id, {"mode": "three_part", "student_ids": [students[0].id], "predicate": "去扫地"})
        broadcast_id = data["broadcast_id"]
        stopped = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts/{broadcast_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["data"]["command"]["kind"] == "broadcast.stop"
        cleared = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts/{broadcast_id}/clear")
        assert cleared.json()["data"]["command"]["kind"] == "broadcast.clear"
        # 停止指令不回写原广播结果：那必须由终端对原命令的回执决定。
        detail = client.get(f"/api/v1/classes/{cls.id}/classroom/broadcasts/{broadcast_id}").json()["data"]
        assert detail["display_status"] == "authorized" and detail["speak_status"] == "authorized"
        missing = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts/not-found/stop")
        assert missing.status_code == 404


def test_broadcast_history_is_class_scoped_and_paginated(client, db, sample):
    cls, other, students = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        for _ in range(3):
            _send(client, cls.id, {"mode": "custom", "text": "请保持安静"})
    listed = client.get(f"/api/v1/classes/{cls.id}/classroom/broadcasts?page=1&page_size=2").json()["data"]
    assert listed["total"] == 3 and len(listed["items"]) == 2
    assert all(row["class_id"] == cls.id for row in listed["items"])
    assert client.get(f"/api/v1/classes/{other.id}/classroom/broadcasts").json()["data"]["total"] == 0
    assert client.get(f"/api/v1/classes/{cls.id}/classroom/broadcasts/{students[0].id}").status_code == 404


def test_idempotency_key_does_not_rebroadcast(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    body = {"mode": "three_part", "student_ids": [students[0].id], "predicate": "去扫地"}
    with online_terminal(db, paired["device_id"], cls.id):
        composed = classroom_broadcast.compose(db, cls.id, BroadcastComposeRequest(**body))
        first, first_command = classroom_broadcast.register(db, composed, requested_by="teacher", source_type="web",
                                                            idempotency_key="same-key")
        db.commit()
        _second, second_command = classroom_broadcast.register(db, composed, requested_by="teacher", source_type="web",
                                                              idempotency_key="same-key")
        db.commit()
    assert first_command.id == second_command.id
    assert db.scalar(select(ClassroomBroadcast).where(ClassroomBroadcast.id == first.id)) is not None
    assert len(list(db.scalars(select(ClassroomBroadcast)))) == 1
