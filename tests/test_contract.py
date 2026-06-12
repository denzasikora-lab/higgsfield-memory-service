from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_turn_roundtrip_contract_shape(client: TestClient, fake_repository) -> None:
    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "u1",
            "messages": [
                {
                    "role": "user",
                    "content": "I live in Berlin.",
                }
            ],
            "timestamp": "2025-03-15T10:00:00Z",
            "metadata": {"source": "test"},
        },
    )

    assert response.status_code == 201
    assert response.json() == {"id": "turn_test_1"}
    assert len(fake_repository.turns) == 1


def test_recall_empty_scaffold_response(client: TestClient) -> None:
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
    assert response.json() == {"context": "", "citations": []}


def test_search_empty_scaffold_response(client: TestClient) -> None:
    response = client.post(
        "/search",
        json={
            "query": "Berlin",
            "session_id": "s1",
            "user_id": "u1",
            "limit": 10,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"results": []}


def test_user_memories_empty_scaffold_response(client: TestClient) -> None:
    response = client.get("/users/u1/memories")

    assert response.status_code == 200
    assert response.json() == {"memories": []}


def test_delete_endpoints(client: TestClient, fake_repository) -> None:
    session_response = client.delete("/sessions/s1")
    user_response = client.delete("/users/u1")

    assert session_response.status_code == 204
    assert user_response.status_code == 204
    assert fake_repository.deleted_sessions == ["s1"]
    assert fake_repository.deleted_users == ["u1"]
