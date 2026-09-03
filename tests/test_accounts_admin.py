from datetime import date

from sqlalchemy import select

from app.config import settings
from app.models.entities import ClassRoom, Homework, User


def _admin_header() -> dict[str, str]:
    assert settings.api_token
    return {"Authorization": f"Bearer {settings.api_token}", "X-ClassClaw-Surface": "web"}


def test_default_admin_login(client):
    logged_in = client.post("/api/v1/auth/login", json={"username": "ADMIN", "password": settings.default_admin_password})
    assert logged_in.status_code == 200
    assert logged_in.json()["data"]["user"]["role"] == "admin"


def test_backend_acceptance_console_is_served(client):
    page = client.get("/app/test.html")
    assert page.status_code == 200
    assert "ClassClaw · API 验收台" in page.text
    assert "后端功能地图" in page.text
    assert "数据库调试" in page.text
    script = client.get("/app/test.js")
    assert script.status_code == 200
    assert "/admin/database/overview" in script.text


def test_admin_creates_unique_teacher_and_teacher_can_login(client, db):
    created = client.post(
        "/api/v1/admin/users",
        json={"username": "Teacher01", "display_name": "李老师"},
        headers=_admin_header(),
    )
    assert created.status_code == 201
    teacher = created.json()["data"]
    assert teacher["username"] == "teacher01"
    assert teacher["role"] == "head_teacher"
    assert teacher["must_change_password"] is True

    duplicate = client.post(
        "/api/v1/admin/users",
        json={"username": "TEACHER01"},
        headers=_admin_header(),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "USERNAME_CONFLICT"

    wrong = client.post("/api/v1/auth/login", json={"username": "teacher01", "password": "wrong"})
    assert wrong.status_code == 401
    logged_in = client.post("/api/v1/auth/login", json={"username": "teacher01", "password": "32767"})
    assert logged_in.status_code == 200
    token = logged_in.json()["data"]["access_token"]
    teacher_headers = {"Authorization": f"Bearer {token}", "X-ClassClaw-Surface": "web"}
    me = client.get("/api/v1/auth/me", headers=teacher_headers)
    assert me.status_code == 200
    assert me.json()["data"]["username"] == "teacher01"
    forbidden = client.get("/api/v1/admin/users", headers=teacher_headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "ADMIN_REQUIRED"


def test_head_teacher_has_one_onboarding_and_one_class_limit(client, db):
    created = client.post("/api/v1/admin/users", json={"username": "teacher02"}, headers=_admin_header()).json()["data"]
    token = client.post("/api/v1/auth/login", json={"username": "teacher02", "password": "32767"}).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}", "X-ClassClaw-Surface": "web"}

    first = client.post("/api/v1/class-onboarding/sessions", json={}, headers=headers)
    assert first.status_code == 201
    second = client.post("/api/v1/class-onboarding/sessions", json={}, headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ONBOARDING_ALREADY_EXISTS"

    db.add(ClassRoom(name="账号绑定班", grade="高一", owner_user_id=created["id"]))
    db.commit()
    limited = client.post("/api/v1/class-onboarding/sessions", json={}, headers=headers)
    assert limited.status_code == 409
    assert limited.json()["error"]["code"] == "CLASS_LIMIT_REACHED"

    db.add(ClassRoom(name="其他账号不可见班", grade="高一"))
    db.commit()
    classes = client.get("/api/v1/classes", headers=headers).json()["data"]
    assert classes["total"] == 1
    assert classes["items"][0]["name"] == "账号绑定班"


def test_admin_assigns_only_one_legacy_class_to_teacher(client, db):
    user = client.post("/api/v1/admin/users", json={"username": "legacyteacher"}, headers=_admin_header()).json()["data"]
    first = ClassRoom(name="历史班一", grade="高一")
    second = ClassRoom(name="历史班二", grade="高二")
    db.add_all([first, second])
    db.commit()
    assigned = client.post(
        f"/api/v1/admin/users/{user['id']}/assign-class",
        json={"class_id": first.id},
        headers=_admin_header(),
    )
    assert assigned.status_code == 200
    rejected = client.post(
        f"/api/v1/admin/users/{user['id']}/assign-class",
        json={"class_id": second.id},
        headers=_admin_header(),
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "CLASS_LIMIT_REACHED"


def test_admin_database_debug_is_read_only_and_redacts_secrets(client, db):
    client.post("/api/v1/admin/users", json={"username": "debugteacher"}, headers=_admin_header())
    overview = client.get("/api/v1/admin/database/overview", headers=_admin_header())
    assert overview.status_code == 200
    table_names = {item["name"] for item in overview.json()["data"]["tables"]}
    assert {"users", "user_sessions", "classes", "class_agent_bindings"} <= table_names

    rows = client.get("/api/v1/admin/database/tables/users", headers=_admin_header())
    assert rows.status_code == 200
    assert rows.json()["data"]["items"]
    assert all(item["password_hash"] == "***" for item in rows.json()["data"]["items"])
    assert db.scalar(select(User).where(User.username == "debugteacher")) is not None


def test_head_teacher_domain_routes_are_scoped_to_owned_class(client, db):
    user = client.post("/api/v1/admin/users", json={"username": "scopedteacher"}, headers=_admin_header()).json()["data"]
    owned = ClassRoom(name="归属班", grade="高一", owner_user_id=user["id"])
    other = ClassRoom(name="其他班", grade="高一")
    db.add_all([owned, other])
    db.flush()
    db.add_all([
        Homework(class_id=owned.id, title="本班作业", subject="语文", assigned_date=date(2026, 8, 30)),
        Homework(class_id=other.id, title="其他班作业", subject="数学", assigned_date=date(2026, 8, 30)),
    ])
    db.commit()
    token = client.post("/api/v1/auth/login", json={"username": "scopedteacher", "password": "32767"}).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    listed = client.get("/api/v1/homework", headers=headers)
    assert listed.status_code == 200
    assert [row["title"] for row in listed.json()["data"]] == ["本班作业"]
    forbidden = client.post(
        "/api/v1/homework",
        headers=headers,
        json={"class_id": other.id, "title": "越权", "subject": "语文", "assigned_date": "2026-08-30"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "CLASS_ACCESS_DENIED"
