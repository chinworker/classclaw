from __future__ import annotations

from datetime import timedelta

from app.models.entities import DutyAssignment, DutySchedule
from app.services import openclaw_bridge
from app.utils.time import today


def test_timetable_page_uses_week_adjustments_base_order(client):
    page = client.get("/app/js/pages/timetablePage.js")
    assert page.status_code == 200
    source = page.text
    weekly_heading = 'el("h3", {}, "本周课表")'
    adjustment_heading = 'el("h3", {}, "课程调整")'
    base_heading = 'el("h3", {}, "基础课表")'
    assert source.index(weekly_heading) < source.index(adjustment_heading) < source.index(base_heading)
    assert "单课调整" in source
    assert "课程互换" in source
    assert "长期调整" in source
    assert "lesson_date_a" in source and "lesson_date_b" in source
    assert "读取调整后的最新安排" in source
    assert "来源始终按基础课表预览" in source
    assert 'class: "week-range"' in source
    assert '"上一周"' in source and '"回到本周"' in source and '"下一周"' in source
    assert "weekOffset * 7" in source
    assert "getDaily(dateString, true)" in source
    assert "预览影响" not in source
    assert "weekdayChecks" not in source
    batch_source = source[source.index("function buildBatchAdjustment"):]
    assert batch_source.index('field("星期", weekdayInput)') < batch_source.index('field("节次", periodInput)')
    assert "replacement_teacher: teacherInput.value.trim() || sourceLesson.teacher" not in source
    assert "replacement_room: roomInput.value.trim() || sourceLesson.room" not in source


def test_timetable_file_preview_and_apply(client, sample, monkeypatch):
    cls, _, _ = sample

    async def analyze(_db, class_id, attachments, *, cancelled=None):
        assert class_id == cls.id
        assert len(attachments) == 1
        assert cancelled is not None
        return {
            "periods": [{"period_no": 1, "name": "第一节", "sort_order": 1, "enabled": True}],
            "items": [{"weekday": 1, "period_no": 1, "subject": "语文", "teacher": "张老师", "room": "101"}],
            "analysis": {"accepted": True, "summary": "识别完成", "warnings": [], "confidence": 0.9, "reasons": ["星期、节次和科目清晰"]},
            "attachments": attachments,
        }

    monkeypatch.setattr(openclaw_bridge, "analyze_timetable_files", analyze)
    preview = client.post(
        f"/api/v1/classes/{cls.id}/timetable/import-preview",
        files={"files": ("课表.csv", "星期,节次,科目\n周一,1,语文", "text/csv")},
    )
    assert preview.status_code == 200
    assert preview.json()["data"]["items"][0]["subject"] == "语文"

    applied = client.post(
        f"/api/v1/classes/{cls.id}/timetable/import-apply",
        json={"periods": preview.json()["data"]["periods"], "items": preview.json()["data"]["items"]},
    )
    assert applied.status_code == 200
    assert applied.json()["data"]["courses_saved"] == 1
    base = client.get(f"/api/v1/classes/{cls.id}/timetable/base")
    assert base.json()["data"][0]["teacher"] == "张老师"
    subjects = client.get(f"/api/v1/classes/{cls.id}/subjects")
    assert [row["name"] for row in subjects.json()["data"]] == ["语文"]


def test_seating_file_preview(client, sample, monkeypatch):
    cls, _, students = sample

    async def analyze(_db, class_id, attachments, *, cancelled=None):
        assert class_id == cls.id
        assert cancelled is not None
        return {
            "rows": 1,
            "cols": 3,
            "layout": [[students[0].id, students[1].id, None]],
            "seated_count": 2,
            "unseated_count": 1,
            "analysis": {"accepted": True, "summary": "识别完成", "warnings": [], "confidence": 1, "reasons": ["行列和学生身份清晰"]},
            "attachments": attachments,
        }

    monkeypatch.setattr(openclaw_bridge, "analyze_seating_files", analyze)
    response = client.post(
        f"/api/v1/classes/{cls.id}/seating/import-preview",
        files={"files": ("座位.txt", "张三 李四", "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["data"]["seated_count"] == 2


def test_seating_versions_can_be_named_renamed_and_deleted(client, sample):
    cls, _, students = sample
    created = client.post(
        f"/api/v1/classes/{cls.id}/seating",
        json={"rows": 1, "cols": 2, "layout": [[students[0].id, students[1].id]]},
    )
    assert created.status_code == 201
    snapshot = created.json()["data"]["snapshot"]
    assert snapshot["name"].startswith("座位表 ")

    renamed = client.patch(f"/api/v1/seating/{snapshot['id']}", json={"name": "期中座位"})
    assert renamed.status_code == 200
    assert renamed.json()["data"]["name"] == "期中座位"

    deleted = client.delete(f"/api/v1/seating/{snapshot['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/classes/{cls.id}/seating/current").json()["data"] is None


def test_static_duty_rule_crud_still_works(client, sample):
    cls, _, _ = sample
    normalized = {"items": [{"name": "扫地", "count": 2}], "workdays": [1, 2, 3, 4, 5], "exclude_students": [], "skip_dates": []}
    saved = client.post(
        "/api/v1/duty/rules",
        json={
            "class_id": cls.id,
            "name": "日常值日",
            "original_text": None,
            "rule_json": normalized,
            "effective_from": "2026-08-30",
            "effective_to": "2026-12-31",
            "status": "active",
        },
    )
    assert saved.status_code == 201
    listed = client.get(f"/api/v1/duty/rules?class_id={cls.id}")
    assert listed.status_code == 200
    assert listed.json()["data"][0]["name"] == "日常值日"

    rule_id = listed.json()["data"][0]["id"]
    updated = client.patch(f"/api/v1/duty/rules/{rule_id}", json={"name": "新版日常值日"})
    assert updated.status_code == 200
    assert updated.json()["data"]["name"] == "新版日常值日"
    assert client.delete(f"/api/v1/duty/rules/{rule_id}").status_code == 200


def test_duty_score_completes_and_previous_day_defaults_to_five(client, db, sample):
    cls, _, students = sample
    schedule = DutySchedule(class_id=cls.id, name="本周值日", start_date=today() - timedelta(days=2), end_date=today(), status="active")
    db.add(schedule)
    db.flush()
    yesterday = DutyAssignment(duty_schedule_id=schedule.id, duty_date=today() - timedelta(days=1), item_name="扫地", student_id=students[0].id)
    current = DutyAssignment(duty_schedule_id=schedule.id, duty_date=today(), item_name="擦黑板", student_id=students[1].id)
    db.add_all([yesterday, current])
    db.commit()

    listed = client.get(f"/api/v1/duty/assignments?class_id={cls.id}&start_date={today() - timedelta(days=1)}&end_date={today()}")
    assert listed.status_code == 200
    assert db.get(DutyAssignment, yesterday.id).score == 5
    assert db.get(DutyAssignment, yesterday.id).status == "completed"

    scored = client.put(f"/api/v1/duty/assignments/{current.id}/score", json={"score": 3})
    assert scored.status_code == 200
    assert scored.json()["data"]["score"] == 3
    assert scored.json()["data"]["status"] == "completed"


def test_web_student_event_is_classified_before_direct_save(client, sample, monkeypatch):
    cls, _, students = sample

    async def analyze(_db, class_id, student_id, event_date, content, subject, *, cancelled=None):
        assert class_id == cls.id and student_id == students[0].id
        assert cancelled is not None
        assert "忘带" in content
        return {
            "event": {
                "class_id": class_id, "student_id": student_id, "event_type": "behavior", "subtype": "forgot_materials",
                "event_date": str(event_date), "subject": subject, "content": content, "sentiment": "negative", "severity": "normal", "source_type": "web",
                "event_time": None, "score_delta": None, "source_message_id": None, "attachment_id": None,
            },
            "summary": "忘带学习用品，判为负向",
            "analysis": {
                "accepted": True,
                "confidence": 0.96,
                "reasons": ["内容明确描述了忘带课本"],
                "warnings": [],
            },
        }

    monkeypatch.setattr(openclaw_bridge, "analyze_student_event", analyze)
    preview = client.post(
        f"/api/v1/classes/{cls.id}/student-events/analyze",
        json={"student_id": students[0].id, "event_date": str(today()), "content": "上课忘带课本", "subject": "语文"},
    )
    assert preview.status_code == 200
    assert preview.json()["data"]["event"]["sentiment"] == "negative"
    saved = client.post("/api/v1/student-events", json=preview.json()["data"]["event"])
    assert saved.status_code == 201
