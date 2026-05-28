import pytest


def test_api_404_returns_standard_json():
    from app import app

    client = app.test_client()
    response = client.get("/api/sessions/not-found")

    assert response.status_code == 404
    assert response.get_json() == {"error": "resource not found"}


def test_create_session_rejects_invalid_mode():
    from app import app

    client = app.test_client()
    response = client.post("/api/sessions", json={"mode": "invalid"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "mode must be design or review"}
