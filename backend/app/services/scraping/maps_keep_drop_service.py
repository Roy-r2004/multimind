"""Strict keep/drop gate for Maps census places.

One low-cost AI call per facility (default ``anthropic/claude-haiku-5.5`` via
OpenRouter) using existing Google
Maps fields, descriptions, and already-crawled website content. When evidence is
insufficient or confidence falls below the configured threshold, a single Sonar
(low search context) fallback call re-judges the candidate.

Three outcomes, not two: ``keep`` (confident match), ``drop`` (confidently
matches one of the exclusion conditions — government, outpatient-only, solo
practitioner, unrelated business, etc.), and ``review`` (genuine
addiction-treatment signal but not yet confirmed residential/ownership —
preserved for Phase 2's real evidence-gathering rather than guessed away).
Both ``keep`` and ``review`` route to Phase 2 detail enrichment/classification;
only ``drop`` stops here. Neither ``keep`` nor ``review`` stamps
``client_eligibility`` as ``eligible`` — that is Phase 1 metadata, not
verified evidence, so the authoritative ``compute_client_eligibility`` result
(and hence export inclusion) is decided once by Phase 2's real classification,
not guessed here. Drop rows become ``unrelated`` + ``excluded`` and are never
enriched further. Nothing is deleted; the decision payload is persisted on the
place for audit.

A fourth, bounded-retry-then-``review`` outcome covers technical failure:
if BOTH the primary classifier and the Sonar fallback fail outright (after
``maps_keep_drop_retry_max_attempts`` attempts each, for transient
connection/timeout errors only), the place is never dropped — it is marked
``review`` with reason ``classifier_unavailable`` so it still reaches Phase 2
and remains findable for an explicit re-judgment via
``maps_admin_service.retry_failed_keep_drop`` once the provider recovers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    MapsCensusRun,
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
    MapsWebsiteCrawlCache,
)
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry
from app.services.scraping.maps_eligibility import (
    compute_client_eligibility,
    derive_legacy_verification_verdict,
)
from app.services.scraping.maps_enrichment_processing_state import MapsEnrichmentPipelineState

logger = logging.getLogger(__name__)

KEEP = "keep"
DROP = "drop"
REVIEW = "review"

_NANO_SOURCE = "nano"
_SONAR_SOURCE = "sonar"
# Distinct from both of the above: the pass never actually judged this place
# at all — every attempt (including retries) hit a transient provider/network
# failure. Lets retry_failed_keep_drop (and anything else) tell "technical
# failure, re-triable" apart from a genuine, evidence-based AI verdict.
_TECHNICAL_FAILURE_SOURCE = "technical_failure"
TECHNICAL_FAILURE_REASON = "classifier_unavailable"

# Connection-level/transient errors worth a bounded retry — never a parsed-
# but-invalid model response (ValidationError/json errors), where retrying
# just burns budget without fixing anything.
_RETRYABLE_ERRORS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.ReadError,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    OSError,
)


class KeepDropEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str = Field(default="maps", max_length=20)
    text: str = Field(default="", max_length=500)
    url: str | None = Field(default=None, max_length=1024)


class KeepDropDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: Literal["keep", "drop", "review"]
    reason: str = Field(default="", max_length=400)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[KeepDropEvidence] = Field(default_factory=list)


def _domain_of(url: str | None) -> str | None:
    host = urlsplit((url or "").strip()).hostname
    return host.lower() if host else None


def _seed_path_of(url: str | None) -> str | None:
    path = urlsplit((url or "").strip()).path or "/"
    return path.rstrip("/") or "/"


def build_keep_drop_payload(place: MapsPlace) -> dict[str, Any]:
    """Existing Google Maps fields + prior classification context (no new fetches)."""
    return {
        "place_id": place.id,
        "name": place.canonical_name or place.raw_name,
        "place_types": place.place_types or [],
        "address": place.formatted_address,
        "city": place.city_name,
        "region": place.region_name,
        "phone": place.international_phone_number,
        "website": place.official_website or place.raw_website,
        "prior_lifecycle": place.lifecycle_status,
        "prior_relevance_reason": place.relevance_reason,
    }


async def _crawl_excerpt_for_place(
    session: AsyncSession, place: MapsPlace, *, max_chars: int
) -> str | None:
    """Reuse already-crawled website content — never fetch the web here."""
    website = place.official_website or place.raw_website
    domain = _domain_of(website)
    if not domain:
        return None
    # A multi-branch domain can hold one crawl-cache row per seed path (see
    # maps_website_crawl_service). Prefer the row that was actually seeded
    # from THIS place's own official_website path; only fall back to any
    # row for the domain (older cache rows, or this place's path never
    # having been crawled yet) rather than returning nothing.
    seed_path = _seed_path_of(website)
    cached = (
        await session.execute(
            select(MapsWebsiteCrawlCache).where(
                MapsWebsiteCrawlCache.normalized_domain == domain,
                MapsWebsiteCrawlCache.seed_path == seed_path,
            )
        )
    ).scalars().first()
    if cached is None:
        cached = (
            await session.execute(
                select(MapsWebsiteCrawlCache).where(
                    MapsWebsiteCrawlCache.normalized_domain == domain
                )
            )
        ).scalars().first()
    if cached is None:
        return None
    parts: list[str] = []
    for page in cached.pages or []:
        text = str((page or {}).get("excerpt") or (page or {}).get("text") or "").strip()
        if text:
            parts.append(text)
    combined = "\n\n".join(parts).strip()
    if not combined:
        return None
    return combined[:max_chars]


def _parse_decision(text: str) -> KeepDropDecision:
    raw = LLMProvider.parse_json_response(text)
    return KeepDropDecision.model_validate(raw)


async def _call_model(
    *,
    prompt_name: str,
    system: str,
    model_slug: str,
    provider: Any,
    max_tokens: int,
    **render_kwargs: Any,
) -> KeepDropDecision:
    prompt = get_prompt_engine().render(prompt_name, **render_kwargs)
    response = await provider.complete(
        system=system,
        user=prompt,
        model=model_slug,
        max_tokens=max_tokens,
    )
    return _parse_decision(response.text or "")


async def _call_model_with_retry(**kwargs: Any) -> KeepDropDecision:
    """Bounded retry, with backoff, for TRANSIENT provider/network failures
    only (``_RETRYABLE_ERRORS``) — a parsed-but-invalid model response
    (ValidationError, JSON errors) is never retried, since trying again
    can't fix a malformed output and would just spend budget for nothing.

    Root cause this closes: Belarus run 5acff6f7-21f8-4427-88a1-38d0a394ce83
    lost 130/1106 discovered places to a single ~60-second DNS blip — every
    in-flight keep/drop call failed once, with no retry at all, and (see
    ``classify_place_keep_drop``) a total failure used to fall back to a
    confident DROP.
    """
    settings = get_settings()
    max_attempts = max(1, int(settings.maps_keep_drop_retry_max_attempts))
    backoff = max(0.0, float(settings.maps_keep_drop_retry_backoff_seconds))
    for attempt in range(max_attempts):
        try:
            return await _call_model(**kwargs)
        except _RETRYABLE_ERRORS:
            if attempt + 1 >= max_attempts:
                raise
            await asyncio.sleep(backoff * (2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover


async def classify_place_keep_drop(
    session: AsyncSession,
    place: MapsPlace,
    *,
    country_code: str,
    country_name: str,
    state_code: str | None = None,
    state_name: str | None = None,
) -> tuple[KeepDropDecision, str]:
    """Return (decision, source). Uncertain judgments become drop, as with the
    original Google Places gate; only a technical failure (both classifiers
    unreachable) becomes review, because nothing actually judged the place."""
    settings = get_settings()
    threshold = float(settings.maps_keep_drop_confidence_threshold)
    payload = build_keep_drop_payload(place)
    crawl_excerpt = await _crawl_excerpt_for_place(
        session, place, max_chars=int(settings.maps_keep_drop_max_crawl_excerpt_chars)
    )

    nano_model = get_model(settings.maps_keep_drop_model)
    nano_provider = get_provider_registry().get_provider(nano_model.provider)

    decision: KeepDropDecision | None = None
    try:
        decision = await _call_model_with_retry(
            prompt_name="scraping/maps_keep_drop.j2",
            system=(
                "You return strict JSON keep/drop decisions for addiction-treatment "
                "facility candidates. Uncertain means drop."
            ),
            model_slug=nano_model.provider_model,
            provider=nano_provider,
            max_tokens=800,
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            state_code=(state_code or "").strip()[:10],
            state_name=(state_name or "").strip()[:120],
            facility_json=json.dumps(payload, ensure_ascii=False),
            crawl_excerpt=crawl_excerpt or "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "maps_keep_drop_nano_failed place_id=%s error=%s", place.id, exc
        )

    if decision is not None and decision.confidence >= threshold:
        return decision, _NANO_SOURCE

    # Insufficient evidence or low confidence → one Sonar fallback (low search
    # context). Sonar is never used when the nano call was already confident.
    if settings.maps_keep_drop_sonar_fallback_enabled:
        sonar_model = get_model("sonar")
        sonar_provider = get_provider_registry().get_provider(sonar_model.provider)
        try:
            sonar_decision = await _call_model_with_retry(
                prompt_name="scraping/maps_keep_drop_sonar.j2",
                system=(
                    "You have live web search. Return a strict JSON keep/drop verdict "
                    "for one addiction-treatment facility candidate. Uncertain means drop."
                ),
                model_slug=sonar_model.provider_model,
                provider=sonar_provider,
                max_tokens=800,
                country_code=(country_code or "XX")[:2].upper(),
                country_name=(country_name or "Unknown")[:120],
                state_code=(state_code or "").strip()[:10],
                state_name=(state_name or "").strip()[:120],
                facility_json=json.dumps(payload, ensure_ascii=False),
            )
            if sonar_decision.confidence >= threshold:
                return sonar_decision, _SONAR_SOURCE
            # Low-confidence sonar verdict: keep its reasoning for audit.
            decision = sonar_decision
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_keep_drop_sonar_failed place_id=%s error=%s", place.id, exc
            )

    if decision is not None and decision.decision == DROP:
        return decision, _SONAR_SOURCE
    if decision is not None:
        # Below-threshold confidence on a keep/review verdict: force drop and
        # keep the model's reasoning for audit. A false keep is worse than a
        # false drop — this is the strictness the Google Places runs had.
        return (
            KeepDropDecision(
                decision=DROP,
                reason=f"uncertain: {decision.reason}"[:400],
                confidence=decision.confidence,
                evidence=decision.evidence,
            ),
            _SONAR_SOURCE,
        )
    # Both the primary classifier and the Sonar fallback failed outright —
    # this place was never actually judged by anything, so it must never
    # become a confident DROP (that would silently and permanently exclude
    # a potentially eligible facility over a transient provider/network
    # failure — confirmed root cause of 130/1106 places lost in Belarus run
    # 5acff6f7-21f8-4427-88a1-38d0a394ce83 to a single ~60s DNS blip).
    # REVIEW keeps it flowing into Phase 2 exactly like a genuine
    # "uncertain" verdict, and the distinct reason/source make it findable
    # for an explicit retry (maps_admin_service.retry_failed_keep_drop) once
    # the provider is healthy again — never silently lost.
    return (
        KeepDropDecision(decision=REVIEW, reason=TECHNICAL_FAILURE_REASON, confidence=0.0),
        _TECHNICAL_FAILURE_SOURCE,
    )


def apply_keep_drop(place: MapsPlace, decision: KeepDropDecision, *, source: str) -> None:
    """Persist the verdict and map it onto the existing funnel fields."""
    place.keep_drop_decision = decision.decision
    place.keep_drop_reason = decision.reason[:400]
    place.keep_drop_confidence = decision.confidence
    place.keep_drop_source = source
    place.keep_drop_evidence = [item.model_dump() for item in decision.evidence][:10]
    place.relevance_reason = decision.reason[:300]
    place.confidence_score = decision.confidence
    place.classification_confidence = decision.confidence

    if decision.decision == KEEP:
        # The strict gate is the eligibility decision, as it was for Google
        # Places runs. compute_client_eligibility returns ELIGIBLE for a kept
        # place unless Phase 2 later finds hard negative evidence (closed,
        # government, outpatient-only, solo practitioner, unrelated) or it
        # sits outside the run's geography.
        place.addiction_focus_confirmed = True
        place.client_eligibility = compute_client_eligibility(place)
        place.lifecycle_status = (
            MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value
            if place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
            else MapsLifecycleStatus.NEEDS_REVIEW.value
        )
        place.is_relevant = True
        place.verification_verdict = derive_legacy_verification_verdict(place.lifecycle_status)
        if place.enrichment_status != MapsPlaceEnrichmentStatus.COMPLETED.value:
            # Still route into Phase 2 classification (PREFILTER_PENDING, not
            # CLASSIFICATION_COMPLETED, which made build_classification_query
            # skip every kept place) so hard negative evidence can veto it.
            place.enrichment_status = MapsPlaceEnrichmentStatus.PENDING.value
            place.enrichment_pipeline_state = (
                MapsEnrichmentPipelineState.PREFILTER_PENDING.value
            )
    elif decision.decision == REVIEW:
        # Genuine addiction-treatment signal but not yet confirmed — unlike a
        # confident KEEP, this must NOT stamp client_eligibility as eligible
        # (that would let an unverified candidate reach the accepted export
        # before Phase 2 ever actually confirms it). Still routes to Phase 2
        # exactly like KEEP, since review's whole point is to let real
        # evidence-gathering resolve it rather than guessing here.
        place.lifecycle_status = MapsLifecycleStatus.NEEDS_REVIEW.value
        place.client_eligibility = MapsClientEligibility.REVIEW.value
        place.is_relevant = True
        place.verification_verdict = "unknown"
        if place.enrichment_status != MapsPlaceEnrichmentStatus.COMPLETED.value:
            # Same fix as the KEEP branch above: route to Phase 2
            # classification (PREFILTER_PENDING), never skip straight past
            # it as CLASSIFICATION_COMPLETED.
            place.enrichment_status = MapsPlaceEnrichmentStatus.PENDING.value
            place.enrichment_pipeline_state = (
                MapsEnrichmentPipelineState.PREFILTER_PENDING.value
            )
    else:
        place.lifecycle_status = MapsLifecycleStatus.UNRELATED.value
        place.client_eligibility = MapsClientEligibility.EXCLUDED.value
        place.is_relevant = False
        place.verification_verdict = "contradicted"
        place.addiction_focus_confirmed = False
        if place.enrichment_status in {
            MapsPlaceEnrichmentStatus.PENDING.value,
            MapsPlaceEnrichmentStatus.RUNNING.value,
            MapsPlaceEnrichmentStatus.FAILED.value,
        }:
            place.enrichment_status = MapsPlaceEnrichmentStatus.SKIPPED.value
        place.enrichment_pipeline_state = MapsEnrichmentPipelineState.FINALIZED.value


def build_keep_drop_query(run_id: str, *, candidates_only: bool = True):
    """Places that still need an AI keep/drop judgment.

    When ``candidates_only`` is True (default for re-passes over existing data),
    only previously-relevant / review / eligible facilities are judged. Already
    excluded noise is never sent to the model — it is bulk-stamped as drop
    without an API call.
    """
    filters = [
        MapsPlace.run_id == run_id,
        MapsPlace.keep_drop_decision.is_(None),
    ]
    if candidates_only:
        filters.append(
            or_(
                MapsPlace.is_relevant.is_(True),
                MapsPlace.client_eligibility.in_(
                    [
                        MapsClientEligibility.REVIEW.value,
                        MapsClientEligibility.ELIGIBLE.value,
                    ]
                ),
                MapsPlace.lifecycle_status.in_(
                    [
                        MapsLifecycleStatus.NEEDS_REVIEW.value,
                        MapsLifecycleStatus.PLAUSIBLE.value,
                        MapsLifecycleStatus.PROBABLE_ELIGIBLE.value,
                        MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
                    ]
                ),
            )
        )
    return select(MapsPlace).where(*filters).order_by(MapsPlace.id)


async def stamp_non_candidate_drops(session_factory, *, run_id: str) -> int:
    """Bulk-drop already-excluded places without an AI call.

    These never belonged on the client list; persisting an explicit drop keeps
    the undecided counter honest without burning nano/Sonar budget.
    """
    stamped = 0
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(MapsPlace).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.keep_drop_decision.is_(None),
                    MapsPlace.is_relevant.is_not(True),
                    MapsPlace.client_eligibility.notin_(
                        [
                            MapsClientEligibility.REVIEW.value,
                            MapsClientEligibility.ELIGIBLE.value,
                        ]
                    ),
                    MapsPlace.lifecycle_status.notin_(
                        [
                            MapsLifecycleStatus.NEEDS_REVIEW.value,
                            MapsLifecycleStatus.PLAUSIBLE.value,
                            MapsLifecycleStatus.PROBABLE_ELIGIBLE.value,
                            MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
                        ]
                    ),
                )
            )
        ).scalars().all()
        for place in rows:
            apply_keep_drop(
                place,
                KeepDropDecision(
                    decision=DROP,
                    reason="preexisting_exclusion: already out of relevant set",
                    confidence=1.0,
                    evidence=[],
                ),
                source="prefilter",
            )
            stamped += 1
        if stamped:
            await session.commit()
    return stamped


async def try_start_keep_drop_pass(session_factory, *, run_id: str) -> MapsCensusRun | None:
    """Atomically claim the right to run a keep/drop pass for this run.

    Returns the claimed run on success, or ``None`` if a pass is already
    "running" for this run_id (or the run does not exist) — in which case
    the caller must not fetch or judge any places. This is the real
    correctness guard against two concurrent starts (the inline pass
    launched by run_census racing an admin-triggered
    revalidate-keeps/retry-failed-keep-drop, or two admin retries fired
    back to back): on Postgres, ``SELECT ... FOR UPDATE`` serializes the
    read-check-write against any other transaction doing the same for the
    same row, so only one caller can ever observe "not running" and flip it
    to "running" — a plain read-then-write (two separate statements with no
    row lock) would leave a window where both callers pass the check before
    either writes, which is exactly the race a non-atomic status check
    cannot close. SQLite (unit tests) has no row-level locking, but tests
    only ever use a single connection, so a plain claim is safe there — the
    same convention already used by claim_batch_place_ids/claim_cell_by_id.
    """
    async with session_factory() as session:
        dialect_name = session.bind.dialect.name if session.bind is not None else ""
        stmt = select(MapsCensusRun).where(MapsCensusRun.id == run_id)
        if dialect_name == "postgresql":
            stmt = stmt.with_for_update()
        run = (await session.execute(stmt)).scalar_one_or_none()
        if run is None:
            return None
        state = dict(run.processing_state or {})
        if state.get("keep_drop_status") == "running":
            return None
        state["keep_drop_status"] = "running"
        state["keep_drop_heartbeat_at"] = datetime.now(UTC).isoformat()
        run.processing_state = state
        await session.commit()
        return run


async def run_keep_drop_pass(
    session_factory,
    *,
    run_id: str,
    country_code: str | None = None,
    country_name: str | None = None,
    state_code: str | None = None,
    state_name: str | None = None,
    candidates_only: bool = True,
) -> dict[str, int]:
    """Resumable keep/drop sweep.

    By default only judges relevant / review / eligible candidates. Already
    excluded noise is stamped drop without an API call. Never rediscovers,
    never deletes rows, never re-judges a place that already has a decision.

    Claims the run's keep_drop_status atomically before doing anything else
    (see try_start_keep_drop_pass) — a second concurrent call for the same
    run_id (e.g. an admin retry racing the inline pass run_census already
    started) returns immediately with skipped_busy=1 and never fetches a
    batch or calls the paid classifier.
    """
    settings = get_settings()
    batch_size = max(1, int(settings.maps_keep_drop_batch_size))
    concurrency = max(1, int(settings.maps_keep_drop_concurrency))
    place_timeout = max(10.0, float(settings.maps_keep_drop_place_timeout_seconds))

    run = await try_start_keep_drop_pass(session_factory, run_id=run_id)
    if run is None:
        async with session_factory() as session:
            exists = await session.get(MapsCensusRun, run_id)
        if exists is None:
            return {"error": 1}
        logger.warning("maps_keep_drop_skipped_already_running run_id=%s", run_id)
        return {"skipped_busy": 1}
    country_code = country_code or run.country_code
    country_name = country_name or run.country_name
    state_code = state_code or run.state_code
    state_name = state_name or run.state_name

    prefiltered = 0
    if candidates_only:
        prefiltered = await stamp_non_candidate_drops(session_factory, run_id=run_id)

    kept = 0
    dropped = 0
    reviewed = 0
    sonar_used = 0

    while True:
        async with session_factory() as session:
            batch = (
                await session.execute(
                    build_keep_drop_query(run_id, candidates_only=candidates_only).limit(
                        batch_size
                    )
                )
            ).scalars().all()
            batch_ids = [place.id for place in batch]
            run = await session.get(MapsCensusRun, run_id)
            if run is not None:
                run.heartbeat_at = datetime.now(UTC)
                state = dict(run.processing_state or {})
                state["keep_drop_heartbeat_at"] = datetime.now(UTC).isoformat()
                run.processing_state = state
            await session.commit()

        if not batch_ids:
            break

        semaphore = asyncio.Semaphore(concurrency)

        async def judge(place_id: str) -> tuple[str, KeepDropDecision, str] | None:
            async with semaphore:
                try:
                    async with session_factory() as session:
                        place = await session.get(MapsPlace, place_id)
                        if place is None or place.keep_drop_decision is not None:
                            return None
                        classify_kwargs: dict[str, Any] = {
                            "country_code": country_code or "XX",
                            "country_name": country_name or "Unknown",
                        }
                        if state_code:
                            classify_kwargs["state_code"] = state_code
                        if state_name:
                            classify_kwargs["state_name"] = state_name

                        decision, source = await asyncio.wait_for(
                            classify_place_keep_drop(
                                session,
                                place,
                                **classify_kwargs,
                            ),
                            timeout=place_timeout,
                        )
                        return place_id, decision, source
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "maps_keep_drop_place_failed place_id=%s error=%s", place_id, exc
                    )
                    return (
                        place_id,
                        KeepDropDecision(
                            decision=DROP,
                            reason=f"keep_drop_error: {exc}"[:400],
                            confidence=0.0,
                        ),
                        _NANO_SOURCE,
                    )

        results = await asyncio.gather(*(judge(place_id) for place_id in batch_ids))

        async with session_factory() as session:
            writable = (
                await session.execute(select(MapsPlace).where(MapsPlace.id.in_(batch_ids)))
            ).scalars().all()
            by_id = {place.id: place for place in writable}
            for result in results:
                if result is None:
                    continue
                place_id, decision, source = result
                place = by_id.get(place_id)
                if place is None or place.keep_drop_decision is not None:
                    continue
                apply_keep_drop(place, decision, source=source)
                if decision.decision == KEEP:
                    kept += 1
                elif decision.decision == REVIEW:
                    reviewed += 1
                else:
                    dropped += 1
                if source == _SONAR_SOURCE:
                    sonar_used += 1
            await session.commit()

    async with session_factory() as session:
        run = await session.get(MapsCensusRun, run_id)
        if run is not None:
            state = dict(run.processing_state or {})
            state["keep_drop_status"] = "completed"
            state["keep_drop_completed_at"] = datetime.now(UTC).isoformat()
            state["keep_drop_stats"] = {
                "kept": kept,
                "dropped": dropped,
                "reviewed": reviewed,
                "sonar": sonar_used,
                "prefiltered": prefiltered,
            }
            run.processing_state = state
            await session.commit()

    logger.info(
        "maps_keep_drop_pass_complete run_id=%s kept=%s dropped=%s reviewed=%s sonar=%s prefiltered=%s",
        run_id,
        kept,
        dropped,
        reviewed,
        sonar_used,
        prefiltered,
    )
    return {
        "kept": kept,
        "dropped": dropped,
        "reviewed": reviewed,
        "sonar": sonar_used,
        "prefiltered": prefiltered,
    }


async def count_keeps_awaiting_enrichment(session: AsyncSession, *, run_id: str) -> int:
    """Keep OR review places still queued for detail enrichment (any pass,
    not just this one) — both route to Phase 2, see apply_keep_drop."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(MapsPlace)
                .where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.keep_drop_decision.in_([KEEP, REVIEW]),
                    MapsPlace.enrichment_status.in_(
                        [
                            MapsPlaceEnrichmentStatus.PENDING.value,
                            MapsPlaceEnrichmentStatus.RUNNING.value,
                        ]
                    ),
                )
            )
        ).scalar_one()
        or 0
    )


async def count_undecided(session: AsyncSession, *, run_id: str) -> int:
    """Count places that still need an AI keep/drop judgment (relevant set only)."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(MapsPlace)
                .where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.keep_drop_decision.is_(None),
                    or_(
                        MapsPlace.is_relevant.is_(True),
                        MapsPlace.client_eligibility.in_(
                            [
                                MapsClientEligibility.REVIEW.value,
                                MapsClientEligibility.ELIGIBLE.value,
                            ]
                        ),
                        MapsPlace.lifecycle_status.in_(
                            [
                                MapsLifecycleStatus.NEEDS_REVIEW.value,
                                MapsLifecycleStatus.PLAUSIBLE.value,
                                MapsLifecycleStatus.PROBABLE_ELIGIBLE.value,
                                MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
                            ]
                        ),
                    ),
                )
            )
        ).scalar_one()
        or 0
    )


async def run_maps_keep_drop_job(ctx: dict, run_id: str) -> None:
    """ARQ entrypoint: keep/drop sweep, then detail-enrich the keeps.

    Standalone entrypoint (admin-triggered /keep-drop, /retry-failed-keep-drop,
    /revalidate-keeps) — unlike the keep/drop pass run inline inside
    run_census (which already has its own active quota tracker), nothing
    else tracks this call's cost, so a tracker is created and merged here.
    """
    del ctx
    from app.db.session import AsyncSessionLocal
    from app.services.scraping.maps_quota_tracker import (
        MapsQuotaTracker,
        merge_quota_metrics,
        set_active_tracker,
    )

    logger.info("maps_keep_drop_job_entered", extra={"run_id": run_id})
    tracker = MapsQuotaTracker()
    set_active_tracker(tracker)
    summary = await run_keep_drop_pass(AsyncSessionLocal, run_id=run_id)
    await merge_quota_metrics(AsyncSessionLocal, run_id=run_id, tracker=tracker)
    if summary.get("error"):
        return
    if summary.get("skipped_busy"):
        # A pass was already running for this run_id (the inline run_census
        # continuation, or another admin retry) — it owns completion
        # handling (enqueueing enrichment / reconciling a drained run) once
        # it actually finishes. Enqueuing enrichment here too would race it.
        logger.info("maps_keep_drop_job_skipped_busy run_id=%s", run_id)
        return
    # Gate on every keep still awaiting enrichment, not just this pass's count:
    # a keep decided by an earlier pass that crashed would otherwise never be
    # enriched, leaving the run stuck at running forever (Bosnia).
    async with AsyncSessionLocal() as session:
        pending_keeps = await count_keeps_awaiting_enrichment(session, run_id=run_id)
    if pending_keeps > 0:
        from app.services.scraping.maps_census_service import maps_census_service

        await maps_census_service._enqueue_job(
            "run_maps_enrichment_batch_job",
            run_id,
            inline_runner=lambda: _run_enrichment_inline(run_id),
        )
    else:
        # No keeps means no enrichment will follow — reconcile now so the run
        # does not sit at status=running with everything already decided.
        from app.services.scraping.maps_census_service import reconcile_run_if_drained

        await reconcile_run_if_drained(run_id)


async def _run_enrichment_inline(run_id: str) -> None:
    from app.services.scraping.maps_census_service import run_maps_enrichment_batch_job

    await run_maps_enrichment_batch_job({}, run_id)


__all__ = [
    "KeepDropDecision",
    "KeepDropEvidence",
    "apply_keep_drop",
    "build_keep_drop_payload",
    "build_keep_drop_query",
    "classify_place_keep_drop",
    "count_undecided",
    "run_keep_drop_pass",
    "run_maps_keep_drop_job",
    "stamp_non_candidate_drops",
    "try_start_keep_drop_pass",
]
