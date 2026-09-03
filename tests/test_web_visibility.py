from __future__ import annotations


def test_morning_briefing_remains_available_to_api_but_is_absent_from_web_ui(client, sample):
    cls = sample[0]
    response = client.get("/api/v1/briefings/morning", params={"class_id": cls.id, "date": "2026-09-07"})

    assert response.status_code == 200
    assert "/briefing" not in client.get("/app/app.js").text
    assert "/briefings/morning" not in client.get("/app/js/pages/dashboard.js").text
    assert client.get("/app/js/pages/briefing.js").status_code == 404
