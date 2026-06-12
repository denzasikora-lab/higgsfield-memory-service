from __future__ import annotations

from src.schemas.requests import SearchRequest


def test_search_request_allows_session_or_user_scope() -> None:
    session_only = SearchRequest(query="Berlin", session_id="session-a", user_id=None)
    user_scoped = SearchRequest(query="Berlin", session_id="session-b", user_id="user-a")

    assert session_only.session_id == "session-a"
    assert session_only.user_id is None
    assert user_scoped.user_id == "user-a"
