from app.services.scraping.loop_watchdog import (
    LoopSnapshot,
    ScrapingLoopWatchdog,
    crawl_pattern_signature,
)


def _snapshot() -> LoopSnapshot:
    return LoopSnapshot(
        total_tasks=10,
        queued_tasks=2,
        terminal_tasks=8,
        source_candidates=20,
        source_documents=7,
        retrieval_attempts=9,
    )


def test_watchdog_stops_same_queued_task_reappearing():
    watchdog = ScrapingLoopWatchdog(repeated_task_stop_threshold=3)

    assert watchdog.observe_round(_snapshot(), ["task-a"]) == []
    watchdog.observe_round(
        LoopSnapshot(11, 2, 9, 21, 8, 10),
        ["task-a"],
    )
    signals = watchdog.observe_round(
        LoopSnapshot(12, 2, 10, 22, 9, 11),
        ["task-a"],
    )

    assert any(signal.severity == "stop" for signal in signals)
    assert any(signal.reason == "repeated_queued_tasks" for signal in signals)


def test_watchdog_warns_then_stops_when_snapshot_never_changes():
    watchdog = ScrapingLoopWatchdog(
        repeated_task_stop_threshold=99,
        stagnant_round_warning_threshold=2,
        stagnant_round_stop_threshold=4,
    )
    snapshot = _snapshot()

    watchdog.observe_round(snapshot, ["a"])
    watchdog.observe_round(snapshot, ["b"])
    warning = watchdog.observe_round(snapshot, ["c"])
    watchdog.observe_round(snapshot, ["d"])
    stopped = watchdog.observe_round(snapshot, ["e"])

    assert any(signal.severity == "warning" for signal in warning)
    assert any(signal.reason == "no_progress" for signal in warning)
    assert any(signal.severity == "stop" for signal in stopped)
    assert any(signal.reason == "no_progress" for signal in stopped)


def test_repeating_url_shape_is_warning_only():
    watchdog = ScrapingLoopWatchdog(url_pattern_warning_threshold=3)

    assert watchdog.observe_url("https://example.test/facility/100", crawl_depth=2) is None
    assert watchdog.observe_url("https://example.test/facility/101", crawl_depth=2) is None
    signal = watchdog.observe_url("https://example.test/facility/102", crawl_depth=2)

    assert signal is not None
    assert signal.severity == "warning"
    assert signal.reason == "repeating_url_pattern"


def test_repeated_identical_content_stops_generated_url_loop():
    watchdog = ScrapingLoopWatchdog(repeated_content_stop_threshold=3)

    assert (
        watchdog.observe_content_hash(
            "a" * 64,
            url="https://example.test/page/1",
            crawl_depth=1,
        )
        is None
    )
    assert (
        watchdog.observe_content_hash(
            "a" * 64,
            url="https://example.test/page/2",
            crawl_depth=2,
        )
        is None
    )
    signal = watchdog.observe_content_hash(
        "a" * 64,
        url="https://example.test/page/3",
        crawl_depth=3,
    )

    assert signal is not None
    assert signal.severity == "stop"
    assert signal.reason == "repeated_identical_content"


def test_crawl_pattern_signature_removes_changing_ids_but_keeps_query_shape():
    first = crawl_pattern_signature(
        "https://example.test/facility/12345? page=2&session=abc"
        .replace("? ", "?")
    )
    second = crawl_pattern_signature(
        "https://example.test/facility/98765?session=xyz&page=9"
    )

    assert first == second
    assert first == "example.test/facility/{id}?page&session"
