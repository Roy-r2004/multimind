from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.multi_reference_context_service import (
    MULTI_REFERENCE_CONTEXT_MAX_CHARS,
    MULTI_REFERENCE_HEADER,
    ExtractedConflict,
    ExtractedEvidence,
    ReferenceCandidate,
    ReferenceSource,
    MultiReferenceContextService,
    apply_conflict_rules,
    guard_epistemic_role,
    render_context,
    select_candidates_under_budget,
    validate_extraction,
)


def candidate(
    evidence_id: str,
    source: str,
    kind: str,
    text: str,
    score: float = 10.0,
    created_at: datetime | None = None,
) -> ReferenceCandidate:
    return ReferenceCandidate(evidence_id, source, kind, text, created_at, score)


def sources(*candidates: ReferenceCandidate) -> list[ReferenceSource]:
    return [
        ReferenceSource("source_1", SimpleNamespace(title="First"), tuple(c for c in candidates if c.source == "source_1")),
        ReferenceSource("source_2", SimpleNamespace(title="Second"), tuple(c for c in candidates if c.source == "source_2")),
    ]


@pytest.mark.parametrize(
    ("kind", "text", "proposed", "expected"),
    [
        ("verdict", "You should move to PostgreSQL.", "user_confirmed", "ai_suggested"),
        ("verdict", "The project uses PostgreSQL.", "user_confirmed", "uncertain"),
        ("rolling_memory", "The system is deployed.", "completed", "uncertain"),
        ("user_message", "We should move to PostgreSQL.", "implemented", "planned"),
        ("user_message", "We implemented PostgreSQL.", "tested", "implemented"),
        ("user_message", "We tested the migration successfully.", "completed", "tested"),
        ("user_message", "We completed the migration.", "completed", "completed"),
        ("user_message", "We removed Redis.", "user_stated", "rejected"),
    ],
)
def test_epistemic_guard_prevents_unsupported_promotion(kind, text, proposed, expected):
    assert guard_epistemic_role(candidate("id", "source_1", kind, text), proposed) == expected


def test_validation_discards_invented_ids_and_wrong_source():
    known = candidate("known", "source_1", "user_message", "We selected PostgreSQL.")
    payload = {
        "evidence": [
            {"evidence_id": "invented", "source": "source_1", "source_kind": "user_message", "epistemic_role": "user_confirmed", "statement": "We selected PostgreSQL.", "relevance": 1, "confidence": 1},
            {"evidence_id": "known", "source": "source_2", "source_kind": "user_message", "epistemic_role": "user_confirmed", "statement": "We selected PostgreSQL.", "relevance": 1, "confidence": 1},
        ]
    }
    evidence, conflicts = validate_extraction(payload, [known])
    assert evidence == []
    assert conflicts == []


def test_valid_empty_extraction_renders_both_titles_concisely():
    rendered = render_context(sources(), [], [])
    assert "First" in rendered and "Second" in rendered
    assert rendered.count("No directly relevant evidence") == 2
    assert len(rendered) <= MULTI_REFERENCE_CONTEXT_MAX_CHARS


def test_fallback_is_globally_bounded_and_labels_derived_sources():
    huge = "postgresql " * 3000
    items = [
        candidate("u1", "source_1", "user_message", huge, 20),
        candidate("v1", "source_1", "verdict", huge, 19),
        candidate("m1", "source_2", "rolling_memory", huge, 18),
    ]
    rendered = render_context(sources(*items), [], [], fallback=True)
    assert len(rendered) <= MULTI_REFERENCE_CONTEXT_MAX_CHARS
    assert "PRIOR AI VERDICT" in rendered
    assert "DERIVED ROLLING MEMORY" in rendered
    assert "Possible conflicts were not automatically resolved" in rendered


def test_candidate_budget_is_deterministic_and_relevance_first():
    old_relevant = candidate("old", "source_1", "user_message", "PostgreSQL selected", 100, datetime(2020, 1, 1, tzinfo=UTC))
    recent_unrelated = candidate("new", "source_1", "user_message", "Lunch", 1, datetime.now(UTC))
    selected = select_candidates_under_budget([recent_unrelated, old_relevant], max_chars=300)
    assert selected[0].evidence_id == "old"


def test_dynamic_allocation_allows_more_relevant_source_more_space():
    items = []
    evidence = []
    for index in range(20):
        source = "source_1" if index < 16 else "source_2"
        item = candidate(f"id-{index}", source, "user_message", f"We selected PostgreSQL option {index}.", 50 if source == "source_1" else 2)
        items.append(item)
        evidence.append(ExtractedEvidence(item, "user_confirmed", item.text, 1.0 if source == "source_1" else 0.2, 0.9))
    rendered = render_context(sources(*items), evidence, [])
    first = rendered.split("### Source 2")[0]
    second = rendered.split("### Source 2")[1]
    assert first.count("option") > second.count("option")
    assert len(rendered) <= MULTI_REFERENCE_CONTEXT_MAX_CHARS


def test_newer_grounded_conflict_marks_older_evidence_superseded():
    older = candidate(
        "old", "source_1", "user_message", "We selected Redis.",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    newer = candidate(
        "new", "source_2", "user_message", "We selected PostgreSQL.",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    evidence = [
        ExtractedEvidence(older, "user_confirmed", older.text, 1, 1),
        ExtractedEvidence(newer, "user_confirmed", newer.text, 1, 1),
    ]
    guarded, conflicts = apply_conflict_rules(
        evidence,
        [ExtractedConflict(("old", "new"), "newer_supersedes_older", "Database choice changed.")],
    )
    assert [item.role for item in guarded] == ["superseded", "user_confirmed"]
    assert conflicts[0].resolution == "newer_supersedes_older"


def test_missing_timestamps_do_not_manufacture_chronology():
    first = candidate("one", "source_1", "user_message", "We selected Redis.")
    second = candidate("two", "source_2", "user_message", "We selected PostgreSQL.")
    guarded, conflicts = apply_conflict_rules(
        [
            ExtractedEvidence(first, "user_confirmed", first.text, 1, 1),
            ExtractedEvidence(second, "user_confirmed", second.text, 1, 1),
        ],
        [ExtractedConflict(("one", "two"), "newer_supersedes_older", "Conflicting choices.")],
    )
    assert all(item.role == "user_confirmed" for item in guarded)
    assert conflicts[0].resolution == "unresolved"


@pytest.mark.asyncio
async def test_invalid_json_gets_exactly_one_repair(monkeypatch):
    service = MultiReferenceContextService()
    calls = 0

    async def complete(_system, _user):
        nonlocal calls
        calls += 1
        return "not json" if calls == 1 else '{"evidence": [], "conflicts": []}'

    monkeypatch.setattr(service, "_complete", complete)
    result = await service._extract("question", sources())
    assert result == {"evidence": [], "conflicts": []}
    assert calls == 2


@pytest.mark.asyncio
async def test_timeout_does_not_trigger_semantic_retry(monkeypatch):
    service = MultiReferenceContextService()
    calls = 0

    async def complete(_system, _user):
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return '{}'

    monkeypatch.setattr(service, "_complete", complete)
    monkeypatch.setattr(
        "app.services.multi_reference_context_service.get_settings",
        lambda: SimpleNamespace(multi_reference_extraction_deadline_seconds=0.01),
    )
    with pytest.raises(TimeoutError):
        await service._extract("question", sources())
    assert calls == 1


@pytest.mark.asyncio
async def test_provider_failure_uses_deterministic_fallback(monkeypatch):
    service = MultiReferenceContextService()
    first = candidate("one", "source_1", "user_message", "PostgreSQL selected", 20)
    second = candidate("two", "source_2", "verdict", "PostgreSQL is recommended", 18)
    loaded = sources(first, second)

    async def load(_db, *, source, chat, question):
        del chat, question
        return next(item for item in loaded if item.source == source)

    async def fail(_question, _sources):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(service, "_load_source", load)
    monkeypatch.setattr(service, "_extract", fail)
    rendered = await service.build(
        None,
        source_chats=[SimpleNamespace(title="First"), SimpleNamespace(title="Second")],
        question="PostgreSQL",
    )
    assert rendered.startswith(MULTI_REFERENCE_HEADER)
    assert "PRIOR AI VERDICT" in rendered
    assert len(rendered) <= MULTI_REFERENCE_CONTEXT_MAX_CHARS
