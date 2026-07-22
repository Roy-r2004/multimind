import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.core.exceptions import (
    InvalidAudioError,
    SilentAudioError,
    TranscriptionBusyError,
    TranscriptionDisabledError,
    TranscriptionModelUnavailableError,
)
from app.services import transcription_service as transcription_module
from app.services.transcription_service import TranscriptionService


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
        "transcription_max_upload_bytes": 1024 * 1024,
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


def audio_file(tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio")
    return path


class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeWhisperModel:
    instances = []
    transcribe_calls = []
    init_sleep = 0.0
    transcribe_sleep = 0.0
    fail_cuda = False
    detected_language = "en"
    texts = [" Hello", "  world "]
    active_calls = 0
    max_active_calls = 0
    active_lock = threading.Lock()
    init_thread_ids = []
    transcribe_thread_ids = []

    def __init__(self, model_name, *, device, compute_type, download_root):
        if self.init_sleep:
            time.sleep(self.init_sleep)
        if self.fail_cuda and device == "cuda":
            raise RuntimeError("CUDA unavailable with stack details")
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        self.__class__.init_thread_ids.append(threading.get_ident())
        self.__class__.instances.append(self)

    def transcribe(self, audio, **kwargs):
        self.__class__.transcribe_calls.append(kwargs)
        self.__class__.transcribe_thread_ids.append(threading.get_ident())
        with self.__class__.active_lock:
            self.__class__.active_calls += 1
            self.__class__.max_active_calls = max(
                self.__class__.max_active_calls,
                self.__class__.active_calls,
            )
        try:
            if self.transcribe_sleep:
                time.sleep(self.transcribe_sleep)
            segments = (FakeSegment(text) for text in self.texts)
            info = SimpleNamespace(
                language=kwargs.get("language") or self.detected_language,
                language_probability=0.91,
                duration=3.5,
            )
            return segments, info
        finally:
            with self.__class__.active_lock:
                self.__class__.active_calls -= 1


@pytest.fixture(autouse=True)
def reset_fake_model():
    FakeWhisperModel.instances = []
    FakeWhisperModel.transcribe_calls = []
    FakeWhisperModel.init_sleep = 0.0
    FakeWhisperModel.transcribe_sleep = 0.0
    FakeWhisperModel.fail_cuda = False
    FakeWhisperModel.detected_language = "en"
    FakeWhisperModel.texts = [" Hello", "  world "]
    FakeWhisperModel.active_calls = 0
    FakeWhisperModel.max_active_calls = 0
    FakeWhisperModel.init_thread_ids = []
    FakeWhisperModel.transcribe_thread_ids = []


@pytest.mark.asyncio
async def test_model_loads_only_once(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)
    path = audio_file(tmp_path)

    await service.transcribe(path)
    await service.transcribe(path)

    assert len(FakeWhisperModel.instances) == 1


@pytest.mark.asyncio
async def test_concurrent_initialization_does_not_create_duplicate_models(tmp_path):
    FakeWhisperModel.init_sleep = 0.05
    service = TranscriptionService(
        settings=make_settings(tmp_path, transcription_concurrency=2),
        model_cls=FakeWhisperModel,
    )
    path = audio_file(tmp_path)

    await asyncio.gather(service.transcribe(path), service.transcribe(path))

    assert len(FakeWhisperModel.instances) == 1


@pytest.mark.asyncio
async def test_explicit_english_passes_language(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    await service.transcribe(audio_file(tmp_path), language="en")

    assert FakeWhisperModel.transcribe_calls[0]["language"] == "en"


@pytest.mark.asyncio
async def test_explicit_french_passes_language(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    await service.transcribe(audio_file(tmp_path), language="fr")

    assert FakeWhisperModel.transcribe_calls[0]["language"] == "fr"


@pytest.mark.asyncio
async def test_arabic_language_is_rejected(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    with pytest.raises(InvalidAudioError):
        await service.transcribe(audio_file(tmp_path), language="ar")


@pytest.mark.asyncio
async def test_unsupported_language_is_rejected(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    with pytest.raises(InvalidAudioError):
        await service.transcribe(audio_file(tmp_path), language="de")


@pytest.mark.asyncio
async def test_auto_detection_passes_language_none(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    await service.transcribe(audio_file(tmp_path))

    assert FakeWhisperModel.transcribe_calls[0]["language"] is None


@pytest.mark.asyncio
async def test_auto_detection_accepts_french(tmp_path):
    FakeWhisperModel.detected_language = "fr"
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    result = await service.transcribe(audio_file(tmp_path))

    assert result.language == "fr"
    assert FakeWhisperModel.transcribe_calls[0]["language"] is None


@pytest.mark.asyncio
async def test_auto_detection_rejects_unsupported_language(tmp_path):
    FakeWhisperModel.detected_language = "de"
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    with pytest.raises(InvalidAudioError):
        await service.transcribe(audio_file(tmp_path))


@pytest.mark.asyncio
async def test_cpu_configuration_selects_medium_int8(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    await service.transcribe(audio_file(tmp_path))

    assert len(FakeWhisperModel.instances) == 1
    model = FakeWhisperModel.instances[0]
    assert model.model_name == "medium"
    assert model.device == "cpu"
    assert model.compute_type == "int8"


@pytest.mark.asyncio
async def test_cuda_configuration_selects_large_v3_turbo_float16(tmp_path):
    service = TranscriptionService(
        settings=make_settings(tmp_path, transcription_device="cuda"),
        model_cls=FakeWhisperModel,
    )

    await service.transcribe(audio_file(tmp_path))

    assert len(FakeWhisperModel.instances) == 1
    model = FakeWhisperModel.instances[0]
    assert model.model_name == "large-v3-turbo"
    assert model.device == "cuda"
    assert model.compute_type == "float16"


@pytest.mark.asyncio
async def test_model_uses_configured_cache_directory(tmp_path):
    cache_dir = tmp_path / "persistent-model-cache"
    service = TranscriptionService(
        settings=make_settings(tmp_path, transcription_model_cache_dir=str(cache_dir)),
        model_cls=FakeWhisperModel,
    )

    await service.initialize()

    assert FakeWhisperModel.instances[0].download_root == str(cache_dir)


@pytest.mark.asyncio
async def test_transcription_uses_performance_focused_defaults(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    await service.transcribe(audio_file(tmp_path))

    assert FakeWhisperModel.transcribe_calls[0]["beam_size"] == 1
    assert FakeWhisperModel.transcribe_calls[0]["vad_filter"] is True


@pytest.mark.asyncio
async def test_segment_text_is_combined_and_whitespace_normalized(tmp_path):
    FakeWhisperModel.texts = ["  Hello\n", "   MultiMind  "]
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    result = await service.transcribe(audio_file(tmp_path))

    assert result.text == "Hello MultiMind"


@pytest.mark.asyncio
async def test_empty_transcription_raises_silent_audio(tmp_path):
    FakeWhisperModel.texts = ["  ", "\n"]
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    with pytest.raises(SilentAudioError):
        await service.transcribe(audio_file(tmp_path))


@pytest.mark.asyncio
async def test_cuda_initialization_failure_falls_back_to_cpu(tmp_path):
    FakeWhisperModel.fail_cuda = True
    service = TranscriptionService(
        settings=make_settings(tmp_path, transcription_device="cuda"),
        model_cls=FakeWhisperModel,
    )

    result = await service.transcribe(audio_file(tmp_path))

    assert result.text == "Hello world"
    assert [model.device for model in FakeWhisperModel.instances] == ["cpu"]
    assert [model.model_name for model in FakeWhisperModel.instances] == ["medium"]
    assert [model.compute_type for model in FakeWhisperModel.instances] == ["int8"]


@pytest.mark.asyncio
async def test_cuda_initialization_failure_raises_when_strict(tmp_path):
    FakeWhisperModel.fail_cuda = True
    service = TranscriptionService(
        settings=make_settings(
            tmp_path,
            transcription_device="cuda",
            transcription_strict_device=True,
        ),
        model_cls=FakeWhisperModel,
    )

    with pytest.raises(TranscriptionModelUnavailableError):
        await service.transcribe(audio_file(tmp_path))


@pytest.mark.asyncio
async def test_inference_runs_outside_main_async_event_loop_thread(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)
    main_thread_id = threading.get_ident()

    await service.transcribe(audio_file(tmp_path))

    assert FakeWhisperModel.transcribe_thread_ids
    assert all(thread_id != main_thread_id for thread_id in FakeWhisperModel.transcribe_thread_ids)


@pytest.mark.asyncio
async def test_model_construction_runs_outside_main_async_event_loop_thread(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)
    main_thread_id = threading.get_ident()

    await service.initialize()

    assert FakeWhisperModel.init_thread_ids
    assert all(thread_id != main_thread_id for thread_id in FakeWhisperModel.init_thread_ids)


@pytest.mark.asyncio
async def test_concurrency_semaphore_limits_simultaneous_calls(tmp_path):
    FakeWhisperModel.transcribe_sleep = 0.05
    service = TranscriptionService(
        settings=make_settings(tmp_path, transcription_concurrency=1),
        model_cls=FakeWhisperModel,
    )
    first = audio_file(tmp_path)
    second = tmp_path / "second.wav"
    second.write_bytes(b"audio")

    await asyncio.gather(service.transcribe(first), service.transcribe(second))

    assert FakeWhisperModel.max_active_calls == 1


@pytest.mark.asyncio
async def test_timeout_raises_domain_error_and_waits_for_worker_completion(tmp_path):
    FakeWhisperModel.transcribe_sleep = 0.05
    service = TranscriptionService(
        settings=make_settings(tmp_path, transcription_timeout_seconds=0.01),
        model_cls=FakeWhisperModel,
    )
    started = time.perf_counter()

    with pytest.raises(transcription_module.TranscriptionTimeoutError):
        await service.transcribe(audio_file(tmp_path))

    assert time.perf_counter() - started >= 0.05
    assert FakeWhisperModel.max_active_calls == 1
    assert FakeWhisperModel.active_calls == 0


@pytest.mark.asyncio
async def test_nowait_capacity_rejects_immediately_when_full(tmp_path):
    FakeWhisperModel.transcribe_sleep = 0.05
    service = TranscriptionService(
        settings=make_settings(tmp_path, transcription_concurrency=1),
        model_cls=FakeWhisperModel,
    )
    first = audio_file(tmp_path)
    second = tmp_path / "second.wav"
    second.write_bytes(b"audio")

    results = await asyncio.gather(
        service.transcribe_nowait(first),
        service.transcribe_nowait(second),
        return_exceptions=True,
    )

    assert sum(isinstance(result, TranscriptionBusyError) for result in results) == 1
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert FakeWhisperModel.max_active_calls == 1


@pytest.mark.asyncio
async def test_nowait_capacity_released_after_success(tmp_path):
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)
    path = audio_file(tmp_path)

    await service.transcribe_nowait(path)
    await service.transcribe_nowait(path)

    assert len(FakeWhisperModel.transcribe_calls) == 2


@pytest.mark.asyncio
async def test_nowait_capacity_released_after_normal_failure(tmp_path):
    FakeWhisperModel.texts = [" "]
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)
    path = audio_file(tmp_path)

    with pytest.raises(SilentAudioError):
        await service.transcribe_nowait(path)

    FakeWhisperModel.texts = ["Recovered"]
    result = await service.transcribe_nowait(path)

    assert result.text == "Recovered"


@pytest.mark.asyncio
async def test_nowait_timeout_holds_capacity_until_worker_finishes(tmp_path):
    FakeWhisperModel.transcribe_sleep = 0.05
    service = TranscriptionService(
        settings=make_settings(tmp_path, transcription_timeout_seconds=0.01),
        model_cls=FakeWhisperModel,
    )
    first = audio_file(tmp_path)
    second = tmp_path / "second.wav"
    second.write_bytes(b"audio")

    started = time.perf_counter()
    timeout_result, busy_result = await asyncio.gather(
        service.transcribe_nowait(first),
        service.transcribe_nowait(second),
        return_exceptions=True,
    )

    assert isinstance(timeout_result, transcription_module.TranscriptionTimeoutError)
    assert isinstance(busy_result, TranscriptionBusyError)
    assert time.perf_counter() - started >= 0.05
    assert FakeWhisperModel.active_calls == 0


@pytest.mark.asyncio
async def test_nowait_capacity_never_exceeds_configured_concurrency(tmp_path):
    FakeWhisperModel.transcribe_sleep = 0.05
    service = TranscriptionService(
        settings=make_settings(tmp_path, transcription_concurrency=2),
        model_cls=FakeWhisperModel,
    )
    paths = [tmp_path / f"audio-{index}.wav" for index in range(3)]
    for path in paths:
        path.write_bytes(b"audio")

    results = await asyncio.gather(
        *(service.transcribe_nowait(path) for path in paths),
        return_exceptions=True,
    )

    assert sum(isinstance(result, TranscriptionBusyError) for result in results) == 1
    assert sum(not isinstance(result, Exception) for result in results) == 2
    assert FakeWhisperModel.max_active_calls <= 2


@pytest.mark.asyncio
async def test_transcript_contents_are_not_logged(tmp_path, monkeypatch):
    FakeWhisperModel.texts = ["secret transcript"]
    events = []

    def record_log(*args, **kwargs):
        events.append((args, kwargs))

    monkeypatch.setattr(transcription_module.logger, "info", record_log)
    monkeypatch.setattr(transcription_module.logger, "warning", record_log)
    service = TranscriptionService(settings=make_settings(tmp_path), model_cls=FakeWhisperModel)

    await service.transcribe(audio_file(tmp_path))

    assert "secret transcript" not in str(events)


@pytest.mark.asyncio
async def test_disabled_transcription_rejects_before_model_loading(tmp_path):
    def forbidden_model(*args, **kwargs):
        raise AssertionError("model should not load")

    service = TranscriptionService(
        settings=make_settings(tmp_path, transcription_enabled=False),
        model_cls=forbidden_model,
    )

    with pytest.raises(TranscriptionDisabledError):
        await service.transcribe(audio_file(tmp_path))


@pytest.mark.asyncio
async def test_failed_initialization_can_retry_later(tmp_path):
    FakeWhisperModel.fail_cuda = True
    service = TranscriptionService(
        settings=make_settings(
            tmp_path,
            transcription_device="cuda",
            transcription_strict_device=True,
        ),
        model_cls=FakeWhisperModel,
    )

    with pytest.raises(TranscriptionModelUnavailableError):
        await service.transcribe(audio_file(tmp_path))

    FakeWhisperModel.fail_cuda = False
    result = await service.transcribe(audio_file(tmp_path))

    assert result.text == "Hello world"
    assert len(FakeWhisperModel.instances) == 1
    assert FakeWhisperModel.instances[0].device == "cuda"
