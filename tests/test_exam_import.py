from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.core.errors import AppError
from app.models.entities import Attachment, Exam, ExamImportedTotal, Score, User
from app.services import academic, accounts, exam_import, openclaw_bridge

HEADER = "班级,姓名,学号,考试,日期,数,班序,级序,总分,总班名,总级名"
LAYOUT = {
    "table": 1, "exam_column": 4, "date_column": 5,
    "subjects": [{"subject": "数学", "score": 6, "class_rank": 7, "grade_rank": 8}],
    "total": {"score": 9, "class_rank": 10, "grade_rank": 11},
}


@pytest.fixture
def teacher(db, sample):
    cls, other, _ = sample
    cls.name = "2025级六班"
    user = User(username="import-teacher", password_hash="unused", role="head_teacher", must_change_password=False)
    db.add(user)
    db.flush()
    cls.owner_user_id = user.id
    db.commit()
    token, _ = accounts.issue_session(db, user)
    return cls, other, user, {"Authorization": f"Bearer {token}"}


def preview(client, teacher, csv_text):
    cls, _, _, headers = teacher
    return client.post(
        f"/api/v1/classes/{cls.id}/exams/import-preview",
        headers=headers, files=[("files", ("scores.csv", csv_text.encode(), "text/csv"))],
    )


def apply(client, teacher, data, *choices):
    cls, _, _, headers = teacher
    return client.post(f"/api/v1/classes/{cls.id}/exams/import-apply", headers=headers, json={
        "preview_id": data["preview_id"], "datasets": choices,
    })


def choice(item, full=100):
    return {"dataset_id": item["id"], "name": item["name"], "exam_date": item["exam_date"], "full_scores": {"数学": full}}


def fake_layout(monkeypatch, *layouts):
    async def extract(prompt, **kwargs):
        assert kwargs["require_extractor"] is True
        assert "张三" not in prompt and "李四" not in prompt and "外班生" not in prompt
        assert "98.25" not in prompt
        return {"datasets": layouts}
    monkeypatch.setattr(openclaw_bridge, "_responses_json", extract)


def test_import_whole_grade_multiple_exams_and_source_ranks(client, db, teacher, monkeypatch):
    headers = teacher[3]
    fake_layout(monkeypatch, LAYOUT)
    rows = [
        HEADER,
        "2025级6班,张三,001,月考,2026-10-01,98.25,2,12,210,3,18",
        "六班,李四,2,月考,2026-10-01,, , ,179,8,68",
        "7班,张三,001,月考,2026-10-01,99,1,1,999,1,1",
        "7班,外班生,001,期中,2026-11-02,100,1,1,999,1,1",
        "6,张三,１,期中,2026-11-02,90,6,56,180,4,41",
        "06,李四,002,期中,2026-11-02,95,3,27,200,2,20",
    ]
    response = preview(client, teacher, "\n".join(rows))
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    data = response.json()["data"]
    first, second = data["datasets"]
    assert first["name"] == "月考" and second["name"] == "期中"
    assert first["excluded_rows"] == second["excluded_rows"] == 1
    assert first["missing_cells"] == 1
    assert first["scores"][0]["score"] == 98.25
    assert first["scores"][0]["grade_rank"] == 12
    assert "外班生" not in response.text and "999" not in str(first["totals"])
    assert db.scalar(select(func.count(Exam.id))) == 0
    assert db.scalar(select(func.count(Attachment.id))) == 0

    bad = apply(client, teacher, data, choice(first), choice(second, full=80))
    assert bad.status_code == 422
    assert db.scalar(select(func.count(Exam.id))) == 0
    written = apply(client, teacher, data, choice(first))
    assert written.status_code == 201, written.text
    exam_id = written.json()["data"]["exams"][0]["id"]
    assert db.scalar(select(func.count(Exam.id))) == 1
    score = db.scalar(select(Score).where(Score.exam_id == exam_id))
    assert (score.score, score.class_rank, score.imported_class_rank, score.imported_grade_rank) == (98.25, 1, 2, 12)
    stats = client.get(f"/api/v1/exams/{exam_id}/statistics", headers=headers).json()["data"]
    assert stats["totals"][0]["imported_total_score"] == 210
    assert stats["totals"][0]["imported_grade_rank"] == 18
    assert stats["totals"][1]["total_score"] is None and stats["totals"][1]["imported_grade_rank"] == 68
    assert apply(client, teacher, data, choice(first)).status_code == 404
    assert client.delete(f"/api/v1/exams/{exam_id}", headers=headers).status_code == 200
    assert db.scalar(select(func.count(ExamImportedTotal.id))) == 0


def test_xlsx_multiple_versions_and_selection_no_duplicate_or_overwrite(client, db, teacher, monkeypatch):
    cls, _, _, headers = teacher
    layout = {**LAYOUT, "exam_column": None, "date_column": None, "name": "期中", "exam_date": "2026-11-02",
              "subjects": [{"subject": "数学", "score": 4}], "total": None}
    fake_layout(monkeypatch, layout, {**layout, "table": 2})
    wb = Workbook()
    wb.active.title = "初版"
    second = wb.create_sheet("复核版")
    for sheet, value in [(wb.active, 79), (second, 89)]:
        sheet.append(["班级", "姓名", "学号", "数学"])
        sheet.append(["6班", "张三", "001", value])
    file = BytesIO()
    wb.save(file)
    response = client.post(f"/api/v1/classes/{cls.id}/exams/import-preview", headers=headers,
                           files={"files": ("scores.xlsx", file.getvalue(), "application/octet-stream")})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    initial, corrected = data["datasets"]
    assert initial["scores"][0]["score"] == 79
    assert corrected["scores"][0]["score"] == 89
    duplicate = apply(client, teacher, data, choice(initial), choice(corrected))
    assert duplicate.status_code == 409 and db.scalar(select(func.count(Exam.id))) == 0
    assert apply(client, teacher, data, choice(corrected)).status_code == 201
    assert db.scalar(select(Score.score)) == 89


def test_invalid_data_is_blocked_with_row_guidance(client, teacher, monkeypatch):
    fake_layout(monkeypatch, LAYOUT)
    rows = [
        HEADER, "6,张三,1,月考,2026-10-01,91,0,2,91,1,3",
        "6,李四,3,月考,2026-10-01,88,1,2,88,1,3",
        "6,张三,1,月考,2026-10-01,88,1,2,88,1,3",
    ]
    result = preview(client, teacher, "\n".join(rows)).json()["data"]
    assert result["preview_id"] is None
    errors = " ".join(result["datasets"][0]["errors"])
    assert "第 2 行" in errors and "排名不是正整数" in errors
    assert "第 3 行" in errors and "无法唯一匹配" in errors
    assert "第 4 行" in errors and "重复" in errors


def test_access_preview_owner_and_roster_revalidation(client, db, teacher, sample, monkeypatch):
    cls, other, user, headers = teacher
    fake_layout(monkeypatch, LAYOUT)
    response = preview(client, teacher, f"{HEADER}\n6,张三,1,月考,2026-10-01,90,1,2,90,1,3")
    data = response.json()["data"]
    assert client.post(f"/api/v1/classes/{other.id}/exams/import-apply", headers=headers, json={
        "preview_id": data["preview_id"], "datasets": [choice(data["datasets"][0])],
    }).status_code == 403
    assert client.post(f"/api/v1/classes/{cls.id}/exams/import-preview", files={
        "files": ("scores.csv", HEADER.encode(), "text/csv"),
    }).status_code == 403
    with pytest.raises(AppError, match="不可访问"):
        exam_import.apply(db, cls.id, f"other-{user.id}", exam_import.ExamImportApply.model_validate({
            "preview_id": data["preview_id"], "datasets": [choice(data["datasets"][0])],
        }))
    sample[2][0].name = "新姓名"
    db.commit()
    assert apply(client, teacher, data, choice(data["datasets"][0])).status_code == 409
    assert db.scalar(select(func.count(Exam.id))) == 0


def test_database_failure_rolls_back_all_selected_exams(client, db, teacher, monkeypatch):
    fake_layout(monkeypatch, LAYOUT)
    rows = [
        HEADER, "6,张三,1,月考,2026-10-01,90,1,2,90,1,3", "6,张三,1,期中,2026-11-01,92,1,2,92,1,3",
    ]
    data = preview(client, teacher, "\n".join(rows)).json()["data"]
    original = academic.save_scores
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise exam_import._error("模拟第二场写入失败")
        return original(*args, **kwargs)

    monkeypatch.setattr(academic, "save_scores", fail_second)
    response = apply(client, teacher, data, *(choice(item) for item in data["datasets"]))
    assert response.status_code == 422
    for model in (Exam, Score, ExamImportedTotal):
        assert db.scalar(select(func.count(model.id))) == 0


def test_extractor_does_not_fallback_to_main(client, teacher):
    response = preview(client, teacher, f"{HEADER}\n6,张三,1,月考,2026-10-01,90,1,2,90,1,3")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EXTRACTOR_UNAVAILABLE"


def test_unknown_header_requires_clear_table_structure(client, teacher):
    response = preview(client, teacher, "任意标题\n甲,乙,丙")
    assert response.status_code == 422
    assert "表头" in response.json()["error"]["message"]


def test_merged_excel_subheaders_keep_subject_rank_columns(client, teacher, monkeypatch):
    cls, _, _, headers = teacher

    async def extract(prompt, **kwargs):
        assert "数学 分数" in prompt and "数学 班名次" in prompt and "数学 级名次" in prompt
        assert "张三" not in prompt
        return {"datasets": [{"table": 1, "subjects": [{"subject": "数学", "score": 4, "class_rank": 5, "grade_rank": 6}]}]}

    monkeypatch.setattr(openclaw_bridge, "_responses_json", extract)
    wb = Workbook()
    sheet = wb.active
    sheet.append(["班级", "姓名", "学号", "数学"])
    sheet.merge_cells("D1:F1")
    sheet.append([None, None, None, "分数", "班名次", "级名次"])
    sheet.append(["6", "张三", "001", 91, 4, 20])
    file = BytesIO()
    wb.save(file)
    response = client.post(f"/api/v1/classes/{cls.id}/exams/import-preview", headers=headers,
                           files={"files": ("merged.xlsx", file.getvalue(), "application/octet-stream")})
    assert response.status_code == 200, response.text
    item = response.json()["data"]["datasets"][0]
    assert item["scores"][0]["grade_rank"] == 20
    assert item["subjects"][0]["class_rank_header"] == "数学 班名次"
