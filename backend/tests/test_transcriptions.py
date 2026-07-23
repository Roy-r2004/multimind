import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import transcriptions
from app.core.config import Settings
from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import (
    AudioTooLongError,
    InvalidAudioError,
    SilentAudioError,
    TranscriptionBusyError,
    TranscriptionDisabledError,
    TranscriptionModelUnavailableError,
    TranscriptionTimeoutError,
)
from app.main import create_app
from app.services.transcription_service import TranscriptionResult


def make_settings(tmp_path, **overrides):
    values = {
        "transcription_enabled": True,
        "transcription_model": "large-v3-turbo",
        "transcription_device": "cpu",
        "transcription_compute_type": "float16",
        "transcription_cpu_model": "medium",
        "transcription_cpu_compute_type": "int8",
        "transcription_strict_device": False,
        "transcription_max_duration_seconds": 600,
        "transcription_max_upload_bytes": 1024,
        "transcription_timeout_seconds": 30,
        "transcription_tmp_dir": str(tmp_path / "transcriptions"),
        "transcription_concurrency": 1,
        "transcription_model_cache_dir": str(tmp_path / "models"),
        "transcription_beam_size": 1,
        "transcription_vad_filter": True,
        "transcription_initial_prompt": "MultiMind, OpenRouter",
    }
    values.update(overrides)
    return Settings(**values)


class FakeTranscriptionService:
    def __init__(self, result=None, error=None, delay=0.0, capacity_limit=None):
        self.result = result or TranscriptionResult(
            text=" Complete transcribed prompt ",
            language="en",
            language_probability=0.97,
            duration_seconds=3.2,
            processing_seconds=0.4,
        )
        self.error = error
        self.delay = delay
        self.capacity_limit = capacity_limit
        self.calls = []
        self.paths = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    async def transcribe_nowait(self, file_path: Path, *, language=None):
        with self._lock:
            if self.capacity_limit is not None and self.active >= self.capacity_limit:
                raise TranscriptionBusyError()
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.calls.append(language)
        self.paths.append(file_path)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            return self.result
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    fake_service = FakeTranscriptionService()
    probes = []

    def fake_probe(path):
        probes.append(path)
        return 3.2

    monkeypatch.setattr(transcriptions, "get_settings", lambda: settings)
    monkeypatch.setattr(transcriptions, "inspect_audio_duration", fake_probe)
    app = create_app()
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user=SimpleNamespace(id="user-1"),
        org_id="org-1",
        role=SimpleNamespace(value="member"),
    )
    app.dependency_overrides[transcriptions.get_transcription_service] = lambda: fake_service
    test_client = TestClient(app, raise_server_exceptions=False)
    test_client.fake_service = fake_service
    test_client.settings = settings
    test_client.probes = probes
    yield test_client


def post_audio(
    client,
    *,
    data=None,
    content=b"audio",
    content_type="audio/webm",
    filename="clip.webm",
):
    return client.post(
        "/api/v1/transcriptions",
        data=data or {},
        files={"file": (filename, content, content_type)},
        headers={"Authorization": "Bearer token", "X-Org-Id": "org-1"},
    )


def test_authentication_is_required():
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/transcriptions",
        files={"file": ("clip.webm", b"audio", "audio/webm")},
    )

    assert response.status_code == 401


def test_valid_webm_upload_returns_expected_schema(client):
    response = post_audio(client)

    assert response.status_code == 200
    assert response.json() == {
        "text": "Complete transcribed prompt",
        "language": "en",
        "language_probability": 0.97,
        "duration_seconds": 3.2,
        "processing_seconds": 0.4,
    }


@pytest.mark.parametrize(
    ("request_language", "service_language"),
    [("auto", None), ("en", "en"), ("fr", "fr")],
)
def test_language_mapping(client, request_language, service_language):
    response = post_audio(client, data={"language": request_language})

    assert response.status_code == 200
    assert client.fake_service.calls == [service_language]


def test_arabic_language_returns_422(client):
    response = post_audio(client, data={"language": "ar"})

    assert response.status_code == 422


def test_unsupported_language_returns_422(client):
    response = post_audio(client, data={"language": "de"})

    assert response.status_code == 422


def test_unsupported_mime_type_returns_415(client):
    response = post_audio(client, content_type="text/plain")

    assert response.status_code == 415
    assert response.json()["error"] == "UNSUPPORTED_AUDIO_TYPE"


def test_mime_type_with_codecs_is_normalized(client):
    response = post_audio(client, content_type="audio/webm;codecs=opus")

    assert response.status_code == 200
    assert len(client.probes) == 1


def test_empty_upload_returns_422(client):
    response = post_audio(client, content=b"")

    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_AUDIO"


def test_upload_larger_than_limit_returns_413(client):
    response = post_audio(
        client,
        content=b"x" * (client.settings.transcription_max_upload_bytes + 1),
    )

    assert response.status_code == 413
    assert response.json()["error"] == "AUDIO_TOO_LARGE"


def test_invalid_audio_returns_422(client, monkeypatch):
    def raise_invalid_audio(_path):
        raise InvalidAudioError()

    monkeypatch.setattr(transcriptions, "inspect_audio_duration", raise_invalid_audio)

    response = post_audio(client)

    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_AUDIO"


def test_audio_longer_than_limit_returns_422(client, monkeypatch):
    def raise_audio_too_long(_path):
        raise AudioTooLongError()

    monkeypatch.setattr(transcriptions, "inspect_audio_duration", raise_audio_too_long)

    response = post_audio(client)

    assert response.status_code == 422
    assert response.json()["error"] == "AUDIO_TOO_LONG"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (SilentAudioError(), 422, "SILENT_AUDIO"),
        (TranscriptionDisabledError(), 503, "TRANSCRIPTION_DISABLED"),
        (TranscriptionModelUnavailableError(), 503, "TRANSCRIPTION_MODEL_UNAVAILABLE"),
        (TranscriptionBusyError(), 429, "TRANSCRIPTION_BUSY"),
        (TranscriptionTimeoutError(), 504, "TRANSCRIPTION_TIMEOUT"),
    ],
)
def test_domain_errors_map_to_statuses(client, error, status, code):
    client.fake_service.error = error

    response = post_audio(client)

    assert response.status_code == status
    assert response.json()["error"] == code


def test_busy_response_contains_retry_after(client):
    client.fake_service.error = TranscriptionBusyError()

    response = post_audio(client)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "5"


def test_temp_file_is_deleted_after_success(client):
    response = post_audio(client)

    assert response.status_code == 200
    assert client.fake_service.paths
    assert not client.fake_service.paths[0].exists()


def test_temp_file_is_deleted_after_service_failure(client):
    client.fake_service.error = SilentAudioError()

    response = post_audio(client)

    assert response.status_code == 422
    assert client.fake_service.paths
    assert not client.fake_service.paths[0].exists()


def test_original_filename_is_never_used_as_temp_path(client):
    response = post_audio(client, filename="../../secret.wav")

    assert response.status_code == 200
    assert client.fake_service.paths
    assert client.fake_service.paths[0].name != "secret.wav"
    assert ".." not in client.fake_service.paths[0].name


def test_transcript_text_is_not_logged(client, monkeypatch):
    client.fake_service.result = TranscriptionResult(
        text="secret transcript",
        language="en",
        language_probability=0.99,
        duration_seconds=1.0,
        processing_seconds=0.2,
    )
    events = []

    def record_log(*args, **kwargs):
        events.append((args, kwargs))

    monkeypatch.setattr(transcriptions.logger, "info", record_log)
    monkeypatch.setattr(transcriptions.logger, "warning", record_log)

    response = post_audio(client)

    assert response.status_code == 200
    assert "secret transcript" not in str(events)


def test_two_simultaneous_requests_cannot_exceed_service_capacity(client):
    client.fake_service.delay = 0.05
    client.fake_service.capacity_limit = 1

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: post_audio(client), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 429]
    assert client.fake_service.max_active <= 1


def test_unexpected_model_exception_returns_safe_500(client):
    client.fake_service.error = RuntimeError("CUDA stack details")

    response = post_audio(client)

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "INTERNAL_ERROR"
    assert "CUDA" not in str(body)


def test_probe_rejects_missing_audio_stream(monkeypatch, tmp_path):
    class FakeContainer:
        duration = None
        streams = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake_av = SimpleNamespace(open=lambda _path: FakeContainer())
    monkeypatch.setitem(sys.modules, "av", fake_av)

    with pytest.raises(InvalidAudioError):
        transcriptions.inspect_audio_duration(tmp_path / "bad.webm")
