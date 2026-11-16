"""Keyset pagination for GET /chats/history (the sidebar's "See all" page).

GET /chats itself stays unbounded and untouched for existing callers; these
tests cover the new paginated+searchable path only.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Chat
from app.services.chat_service import chat_service
from tests.conftest import AuthContext


async def _create_chat(
    db, auth: AuthContext, *, title: str, updated_at: datetime
) -> Chat:
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title=title)
    db.add(chat)
    await db.flush()
    chat.updated_at = updated_at
    await db.flush()
    return chat


@pytest.mark.asyncio
async def test_first_page_is_newest_first_and_matches_unbounded_list(db, auth):
    base = datetime.now(UTC)
    chats = [
        await _create_chat(db, auth, title=f"Chat {i}", updated_at=base - timedelta(minutes=i))
        for i in range(5)
    ]

    page = await chat_service.list_chats_page(db, auth, limit=20)

    assert [item.id for item in page.items] == [c.id for c in chats]
    assert page.has_more is False
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_pagination_reaches_the_oldest_chat_across_hundreds_of_conversations(db, auth):
    total = 230
    page_size = 20
    base = datetime.now(UTC)
    # Descending timestamps so chat 0 is newest and chat `total - 1` is oldest.
    chats = [
        await _create_chat(db, auth, title=f"Chat {i}", updated_at=base - timedelta(seconds=i))
        for i in range(total)
    ]

    collected: list[str] = []
    cursor_updated_at = None
    cursor_id = None
    pages_fetched = 0
    while True:
        page = await chat_service.list_chats_page(
            db,
            auth,
            limit=page_size,
            cursor_updated_at=cursor_updated_at,
            cursor_id=cursor_id,
        )
        collected.extend(item.id for item in page.items)
        pages_fetched += 1
        assert pages_fetched <= total  # guard against an infinite loop bug
        if not page.has_more:
            assert page.next_cursor is None
            break
        assert page.next_cursor is not None
        cursor_updated_at = page.next_cursor.updated_at
        cursor_id = page.next_cursor.id

    assert collected == [c.id for c in chats]
    assert collected[-1] == chats[-1].id  # the oldest stored chat was reached


@pytest.mark.asyncio
async def test_identical_timestamps_get_a_deterministic_tie_break_with_no_gaps_or_dupes(db, auth):
    same_instant = datetime.now(UTC)
    chats = [
        await _create_chat(db, auth, title=f"Tied {i}", updated_at=same_instant) for i in range(7)
    ]
    expected_order = sorted((c.id for c in chats), reverse=True)  # Chat.id desc tie-break

    collected: list[str] = []
    cursor_updated_at = None
    cursor_id = None
    while True:
        page = await chat_service.list_chats_page(
            db, auth, limit=3, cursor_updated_at=cursor_updated_at, cursor_id=cursor_id
        )
        collected.extend(item.id for item in page.items)
        if not page.has_more:
            break
        cursor_updated_at = page.next_cursor.updated_at
        cursor_id = page.next_cursor.id

    assert collected == expected_order
    assert len(collected) == len(set(collected))  # no duplicates across page boundaries


@pytest.mark.asyncio
async def test_search_matches_against_the_entire_stored_history_not_just_one_page(db, auth):
    base = datetime.now(UTC)
    needle = await _create_chat(
        db, auth, title="Budget planning notes", updated_at=base - timedelta(days=100)
    )
    for i in range(40):
        await _create_chat(db, auth, title=f"Unrelated chat {i}", updated_at=base - timedelta(minutes=i))

    page = await chat_service.list_chats_page(db, auth, limit=20, q="budget")

    assert [item.id for item in page.items] == [needle.id]
    assert page.has_more is False


@pytest.mark.asyncio
async def test_other_orgs_chats_are_never_returned(db, auth):
    from tests.conftest import create_other_auth

    other_auth = await create_other_auth(db)
    await _create_chat(db, other_auth, title="Other org chat", updated_at=datetime.now(UTC))
    mine = await _create_chat(db, auth, title="My chat", updated_at=datetime.now(UTC))

    page = await chat_service.list_chats_page(db, auth, limit=20)

    assert [item.id for item in page.items] == [mine.id]


@pytest.mark.asyncio
async def test_unbounded_list_chats_is_unaffected_by_pagination_support(db, auth):
    base = datetime.now(UTC)
    chats = [
        await _create_chat(db, auth, title=f"Chat {i}", updated_at=base - timedelta(minutes=i))
        for i in range(25)
    ]

    listed = await chat_service.list_chats(db, auth)

    assert [c.id for c in listed] == [c.id for c in chats]
