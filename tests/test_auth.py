from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.dependencies import get_repository
from src.db.config import get_settings
from src.main import create_app


def test_memory_endpoints_allow_requests_when_auth_token_is_empty(client: TestClient) -> None:
    response = client.post(
        "/recall",
        json={
            "query": "Where does the user live?",
            "session_id": "s1",
            "user_id": "u1",
            "max_tokens": 128,
        },
    )

    assert response.status_code == 200


def test_memory_endpoints_require_bearer_token_when_configured(
    monkeypatch,
    fake_repository,
) -> None:
    monkeypatch.setenv("MEMORY_AUTH_TOKEN", "secret")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: fake_repository

    with TestClient(app) as client:
        unauthorized = client.post(
            "/recall",
            json={"query": "Where?", "session_id": "s1", "max_tokens": 128},
        )
        authorized = client.post(
            "/recall",
            headers={"Authorization": "Bearer secret"},
            json={"query": "Where?", "session_id": "s1", "max_tokens": 128},
        )
        health = client.get("/health")

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert health.status_code == 200
    app.dependency_overrides.clear()
    get_settings.cache_clear()
