from __future__ import annotations

from fastapi.testclient import TestClient


def test_turn_rejects_missing_session_id(client: TestClient) -> None:
    response = client.post(
        "/turns",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "timestamp": "2025-03-15T10:00:00Z",
            "metadata": {},
        },
    )

    assert response.status_code == 422


def test_turn_rejects_empty_messages(client: TestClient) -> None:
    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "messages": [],
            "timestamp": "2025-03-15T10:00:00Z",
            "metadata": {},
        },
    )

    assert response.status_code == 422


def test_turn_rejects_unknown_role(client: TestClient) -> None:
    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "messages": [{"role": "system", "content": "Nope"}],
            "timestamp": "2025-03-15T10:00:00Z",
            "metadata": {},
        },
    )

    assert response.status_code == 422


def test_bad_json_does_not_crash_service(client: TestClient) -> None:
    response = client.post(
        "/turns",
        content="{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code in {400, 422}
