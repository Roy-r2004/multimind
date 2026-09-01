"""Chat image attachments and dedicated vision-analysis flow tests."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import InvalidAttachmentError, UnsupportedAttachmentTypeError
from app.db.models import Chat, ChatAttachment, Strategy, Turn
from app.db.session import get_db
from app.llm.orchestrator import TurnContext
from app.llm.providers import LLMResponse, OpenRouterProvider
from app.main import create_app
from app.schemas.api import TurnCreateRequest
from app.services.attachment_types import (
    validate_attachment_content_type,
    validate_attachment_filename,
    validate_image_magic_bytes,
)
from app.services.chat_attachment_text import extract_attachment_text
from app.services.chat_service import chat_service
from app.services.chat_vision import (
    ImageAnalysisError,
    VisionImage,
    analyze_images_once,
    build_openrouter_user_content,
    cached_image_context_from_attachments,
    ensure_image_context_for_turn,
    extract_image_context_block,
    load_vision_images_for_turn,
    merge_image_context_into_instructions,
)
from tests.conftest import create_model_set

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP_BYTES = b"RIFF" + (24).to_bytes(4, "little") + b"WEBP" + b"\x00" * 12

SAMPLE_CONTEXT = """IMAGE CONTEXT
Image 1 (photo.png):
Description:
A red circle on a white background.
Visible text:
none
Tables / structured data:
none
Important visual details:
Solid red circle centered.
Question-relevant observations:
The shape is a circle, not a square."""


async def _create_chat(db: AsyncSession, auth: AuthContext) -> Chat:
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Vision chat")
    db.add(chat)
    await db.flush()
    return chat


async def _client_for(db: AsyncSession, auth: AuthContext) -> AsyncClient:
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = lambda: auth
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _upload(
    client: AsyncClient,
    chat_id: str,
    *,
    filename: str,
    content: bytes,
    content_type: str,
):
    return await client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": (filename, BytesIO(content), content_type)},
    )


def test_accepted_image_extensions_and_mime():
    for name, ext, mime in [
        ("a.png", ".png", "image/png"),
        ("b.jpg", ".jpg", "image/jpeg"),
        ("c.jpeg", ".jpeg", "image/jpeg"),
        ("d.webp", ".webp", "image/webp"),
    ]:
        basename, got_ext = validate_attachment_filename(name)
        assert basename == name
        assert got_ext == ext
        assert validate_attachment_content_type(mime, name, ext) in {
            "image/png",
            "image/jpeg",
            "image/webp",
        }


def test_invalid_image_mime_rejected():
    with pytest.raises(UnsupportedAttachmentTypeError):
        validate_attachment_content_type("text/plain", "a.png", ".png")


def test_invalid_image_magic_rejected():
    with pytest.raises(InvalidAttachmentError):
        validate_image_magic_bytes(b"not-an-image!!!!!!!!", ".png")


def test_image_extraction_skips_text_decode():
    excerpt, status = extract_attachment_text(PNG_BYTES, ".png")
    assert excerpt is None
    assert status == "image"


def test_text_extraction_still_works():
    excerpt, status = extract_attachment_text(b"hello docs", ".txt")
    assert excerpt == "hello docs"
    assert status == "ready"


def test_multimodal_payload_for_dedicated_vision_model():
    images = [
        VisionImage(filename="a.png", media_type="image/png", data_base64="AAA"),
        VisionImage(filename="b.jpg", media_type="image/jpeg", data_base64="BBB"),
    ]
    content = build_openrouter_user_content("What is in these images?", images)
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "What is in these images?"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,AAA")
    assert content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,BBB")


def test_merge_and_extract_image_context_block():
    merged = merge_image_context_into_instructions("Org rules", SAMPLE_CONTEXT)
    assert "Org rules" in merged
    assert "### IMAGE CONTEXT" in merged
    assert extract_image_context_block(merged).startswith("IMAGE CONTEXT")


def test_cached_context_requires_all_images_ready():
    ready = ChatAttachment(
        id="1",
        org_id="o",
        chat_id="c",
        uploaded_by_user_id="u",
        filename="a.png",
        stored_name="a.png",
        content_type="image/png",
        size_bytes=10,
        relative_path="o/c/a.png",
        text_excerpt=SAMPLE_CONTEXT,
        excerpt_status="ready",
    )
    pending = ChatAttachment(
        id="2",
        org_id="o",
        chat_id="c",
        uploaded_by_user_id="u",
        filename="b.png",
        stored_name="b.png",
        content_type="image/png",
        size_bytes=10,
        relative_path="o/c/b.png",
        text_excerpt=None,
        excerpt_status="image",
    )
    assert cached_image_context_from_attachments([ready]) == SAMPLE_CONTEXT
    assert cached_image_context_from_attachments([ready, pending]) is None


@pytest.mark.asyncio
async def test_analyze_images_once_sends_raw_images(monkeypatch):
    calls: list[dict] = []

    class _Provider:
        async def complete(self, **kwargs):
            calls.append(kwargs)
            return LLMResponse(text=SAMPLE_CONTEXT, tokens_input=1, tokens_output=1)

    monkeypatch.setattr(
        "app.llm.providers.get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _name: _Provider()),
    )
    monkeypatch.setattr(get_settings(), "chat_image_analysis_model", "gemini")

    images = [
        VisionImage(filename="photo.png", media_type="image/png", data_base64="QQ=="),
        VisionImage(filename="chart.jpg", media_type="image/jpeg", data_base64="Qg=="),
    ]
    text = await analyze_images_once(images=images, user_message="What color?")
    assert text.startswith("IMAGE CONTEXT")
    assert len(calls) == 1
    assert calls[0]["user"].startswith("User question:")
    assert calls[0]["images"] is images
    assert "photo.png" in calls[0]["user"]
    assert "chart.jpg" in calls[0]["user"]
    assert calls[0]["model"] == "google/gemini-2.5-pro"


@pytest.mark.asyncio
async def test_ensure_image_context_analyzes_once_and_caches(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(tmp_path / "attachments"))
    model_set = await create_model_set(db, auth, models=["deepseek", "llama"])
    chat = await _create_chat(db, auth)

    async with await _client_for(db, auth) as client:
        response = await _upload(
            client,
            chat.id,
            filename="photo.png",
            content=PNG_BYTES,
            content_type="image/png",
        )
    assert response.status_code == 201
    att_id = response.json()["id"]

    turn = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(
            user_message="What color is the circle?",
            model_set_id=model_set.slug,
            attachment_ids=[att_id],
        ),
    )

    calls: list[int] = []

    async def _fake_analyze(*, images, user_message):
        calls.append(len(images))
        assert user_message == "What color is the circle?"
        assert images[0].filename == "photo.png"
        assert images[0].data_base64
        return SAMPLE_CONTEXT

    monkeypatch.setattr(
        "app.services.chat_vision.analyze_images_once",
        _fake_analyze,
    )

    turn_row = (await db.execute(select(Turn).where(Turn.id == turn.id))).scalar_one()
    first = await ensure_image_context_for_turn(db, turn=turn_row, org_id=auth.org_id)
    second = await ensure_image_context_for_turn(db, turn=turn_row, org_id=auth.org_id)
    assert first == SAMPLE_CONTEXT
    assert second == SAMPLE_CONTEXT
    assert calls == [1]

    active = await db.get(ChatAttachment, att_id)
    historical = (
        await db.execute(select(ChatAttachment).where(ChatAttachment.turn_id == turn.id))
    ).scalar_one()
    assert active is not None and active.turn_id is None
    assert active.excerpt_status == "image"
    assert active.text_excerpt is None
    assert historical.id != active.id
    assert historical.excerpt_status == "ready"
    assert historical.text_excerpt == SAMPLE_CONTEXT


@pytest.mark.asyncio
async def test_failed_image_analysis_marks_turn_failed(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(tmp_path / "attachments"))
    model_set = await create_model_set(db, auth)
    chat = await _create_chat(db, auth)
    async with await _client_for(db, auth) as client:
        uploaded = await _upload(
            client,
            chat.id,
            filename="photo.png",
            content=PNG_BYTES,
            content_type="image/png",
        )
    turn = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(
            user_message="Describe",
            model_set_id=model_set.slug,
            attachment_ids=[uploaded.json()["id"]],
        ),
    )

    async def _boom(*, images, user_message):
        raise ImageAnalysisError("Image analysis failed. preserved")

    monkeypatch.setattr("app.services.chat_vision.analyze_images_once", _boom)

    events = []
    async for event in chat_service.execute_turn_stream(db, auth, turn.id):
        events.append(event)

    failed = [e for e in events if e.get("type") == "turn_failed"]
    assert failed
    assert failed[0]["data"]["code"] == "IMAGE_ANALYSIS_FAILED"
    turn_row = (await db.execute(select(Turn).where(Turn.id == turn.id))).scalar_one()
    assert turn_row.status.value == "failed"
    assert "Image analysis failed" in (turn_row.error_message or "")
    # Attachment preserved and still linked.
    att = (
        await db.execute(
            select(ChatAttachment).where(ChatAttachment.turn_id == turn.id)
        )
    ).scalar_one()
    assert att.filename == "photo.png"
    assert att.excerpt_status == "image"


@pytest.mark.asyncio
async def test_council_receives_image_context_not_raw_images(monkeypatch):
    """Every council model gets IMAGE CONTEXT via custom_instructions (no images kw)."""
    captured: list[dict] = []

    class _Provider:
        async def complete(self, **kwargs):
            captured.append(kwargs)
            if kwargs.get("user") == "Produce the verdict JSON now.":
                return LLMResponse(
                    text='{"text":"ok","reason":"context used"}',
                    tokens_input=1,
                    tokens_output=1,
                )
            return LLMResponse(
                text="Based on IMAGE CONTEXT the circle is red. CONFIDENCE: 90",
                tokens_input=1,
                tokens_output=1,
                confidence=90,
            )

    monkeypatch.setattr(
        "app.llm.orchestrator.get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _n: _Provider()),
    )

    # Smoke: TurnContext no longer carries vision_images.
    ctx = TurnContext(
        turn_id="t",
        chat_id="c",
        org_id="o",
        project_id=None,
        user_message="What color?",
        model_ids=["deepseek"],
        verdict_model_id="gpt-4.1",
        strategy=Strategy.SYNTHESIZE,
        model_set_name="mixed",
        council_runtime_context=merge_image_context_into_instructions(None, SAMPLE_CONTEXT),
        skip_answer_seed=True,
    )
    assert getattr(ctx, "vision_images", None) in (None, [])
    assert "IMAGE CONTEXT" in (ctx.council_runtime_context or "")
    assert "red circle" in (ctx.council_runtime_context or "").lower()


@pytest.mark.asyncio
async def test_multiple_images_produce_separated_context(monkeypatch):
    multi = """IMAGE CONTEXT
Image 1 (one.png):
Description:
First
Visible text:
A
Tables / structured data:
none
Important visual details:
d1
Question-relevant observations:
o1

Image 2 (two.jpg):
Description:
Second
Visible text:
B
Tables / structured data:
none
Important visual details:
d2
Question-relevant observations:
o2
"""

    class _Provider:
        async def complete(self, **kwargs):
            assert len(kwargs["images"]) == 2
            return LLMResponse(text=multi, tokens_input=1, tokens_output=1)

    monkeypatch.setattr(
        "app.llm.providers.get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _n: _Provider()),
    )
    text = await analyze_images_once(
        images=[
            VisionImage(filename="one.png", media_type="image/png", data_base64="AA"),
            VisionImage(filename="two.jpg", media_type="image/jpeg", data_base64="BB"),
        ],
        user_message="Compare",
    )
    assert "Image 1 (one.png)" in text
    assert "Image 2 (two.jpg)" in text


@pytest.mark.asyncio
async def test_png_upload_and_turn_link(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    attach_dir = tmp_path / "attachments"
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(attach_dir))
    model_set = await create_model_set(db, auth)
    chat = await _create_chat(db, auth)

    async with await _client_for(db, auth) as client:
        response = await _upload(
            client,
            chat.id,
            filename="photo.png",
            content=PNG_BYTES,
            content_type="image/png",
        )
    assert response.status_code == 201
    body = response.json()
    assert body["excerpt_status"] == "image"
    assert body["text_excerpt"] is None

    turn = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(
            user_message="Describe this image",
            model_set_id=model_set.slug,
            attachment_ids=[body["id"]],
        ),
    )
    active = await db.get(ChatAttachment, body["id"])
    historical = (
        await db.execute(select(ChatAttachment).where(ChatAttachment.turn_id == turn.id))
    ).scalar_one()
    assert active is not None and active.turn_id is None
    assert historical.id != active.id
    assert historical.relative_path == active.relative_path
    assert (attach_dir / historical.relative_path).read_bytes() == PNG_BYTES

    images = await load_vision_images_for_turn(db, turn_id=turn.id, org_id=auth.org_id)
    assert len(images) == 1
    assert images[0].media_type == "image/png"


@pytest.mark.asyncio
async def test_text_upload_regression_still_works(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(tmp_path / "attachments"))
    chat = await _create_chat(db, auth)
    async with await _client_for(db, auth) as client:
        response = await _upload(
            client,
            chat.id,
            filename="notes.txt",
            content=b"still works",
            content_type="text/plain",
        )
    assert response.status_code == 201
    body = response.json()
    assert body["excerpt_status"] == "ready"
    assert body["text_excerpt"] == "still works"


@pytest.mark.asyncio
async def test_openrouter_payload_still_supports_images_kw(monkeypatch):
    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "I see a cat. CONFIDENCE: 90"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "cost": 0.01},
            }

        @property
        def text(self):
            return ""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers=None, json=None):
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setattr("app.llm.providers.httpx.AsyncClient", _FakeClient)
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")

    provider = OpenRouterProvider()
    images = [VisionImage(filename="cat.png", media_type="image/png", data_base64="QQ==")]
    await provider.complete(
        system="You are helpful.",
        user="What animal is this?",
        model="google/gemini-2.5-pro",
        images=images,
    )
    user_content = captured["json"]["messages"][1]["content"]
    assert isinstance(user_content, list)
    assert user_content[1]["image_url"]["url"] == "data:image/png;base64,QQ=="


@pytest.mark.asyncio
async def test_bad_image_magic_rejected_on_upload(
    db: AsyncSession, auth: AuthContext, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "chat_attachment_dir", str(tmp_path / "attachments"))
    chat = await _create_chat(db, auth)
    async with await _client_for(db, auth) as client:
        response = await _upload(
            client,
            chat.id,
            filename="fake.png",
            content=b"not-png-content-at-all!!!!!!",
            content_type="image/png",
        )
    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_ATTACHMENT"
