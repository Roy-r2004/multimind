"""Guards that keep long-lived responses from holding pooled DB connections.

A never-ending endpoint (SSE) must not take a session via ``Depends(get_db)``: FastAPI
only unwinds yield-dependencies after the response finishes, so the connection would
stay checked out for the whole stream and the pool would drain one viewer at a time.
"""

from fastapi.dependencies.utils import get_dependant

from app.api.v1.chats import stream_turn
from app.api.v1.scraping.executions import list_facilities, stream_events
from app.core.config import get_settings
from app.db.session import get_db

STREAMING_ENDPOINTS = (stream_events, stream_turn)


def _dependency_calls(endpoint) -> set:
    """Every callable FastAPI would resolve for this endpoint, at any nesting depth."""
    calls = set()
    pending = [get_dependant(path="/", call=endpoint)]
    while pending:
        dependant = pending.pop()
        if dependant.call is not None:
            calls.add(dependant.call)
        pending.extend(dependant.dependencies)
    return calls


def test_streaming_endpoints_do_not_hold_a_pooled_session():
    for endpoint in STREAMING_ENDPOINTS:
        assert get_db not in _dependency_calls(endpoint), (
            f"{endpoint.__name__} injects get_db; a streaming response pins that "
            "connection for its entire lifetime and exhausts the pool"
        )


def test_detector_sees_the_session_dependency_on_a_regular_endpoint():
    # Sanity check that the assertion above can actually fail when get_db is present.
    assert get_db in _dependency_calls(list_facilities)


def test_pool_is_sized_above_a_single_page_request_burst():
    # The execution detail page fires 9 parallel requests per viewer, so the stock
    # 5 + 10 pool would queue past pool_timeout with only a couple of tabs open.
    settings = get_settings()
    assert settings.database_pool_size >= 9
    assert settings.database_pool_size + settings.database_max_overflow >= 30
    assert settings.database_pool_recycle_seconds > 0
