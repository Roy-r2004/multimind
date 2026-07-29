"""Final AI cleanup: drop non-rehabs / bad sources / duplicates and fix wrong websites."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.models import (
    RehabilitationFacility,
    RehabilitationFacilityContact,
    RehabilitationFacilitySourceLink,
)
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry
from app.services.scraping.result_metrics import normalized_publication_class
from app.services.scraping.url_canonicalization import UrlRejected, canonicalize_discovery_url

logger = logging.getLogger(__name__)

CLEANUP_ACTIONS = {
    "keep",
    "exclude_not_rehab",
    "exclude_bad_source",
    "exclude_duplicate",
}
DEFAULT_BATCH_SIZE = 12
# Hard cap around each provider call so a hung OpenRouter request cannot wedge the
# whole cleanup job (and starve the background heartbeat) past this window.
BATCH_LLM_TIMEOUT_SECONDS = 90.0
MAX_REASON_LEN = 200


class FacilityCleanupDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facility_id: str = Field(min_length=1, max_length=36)
    action: str = Field(min_length=1, max_length=40)
    keep_facility_id: str | None = Field(default=None, max_length=36)
    corrected_website: str | None = Field(default=None, max_length=512)
    reason: str = Field(default="", max_length=300)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: Any) -> str:
        text = str(value or "").strip().casefold()
        if text not in CLEANUP_ACTIONS:
            return "keep"
        return text

    @field_validator("facility_id", "keep_facility_id", mode="before")
    @classmethod
    def trim_ids(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("corrected_website", "reason", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            return str(value)
        return value.strip()


class FacilityCleanupPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decisions: list[FacilityCleanupDecision] = Field(default_factory=list)


class FacilityAiCleanupService:
    async def run_for_execution(
        self,
        db: AsyncSession,
        *,
        execution_id: str,
        country_code: str,
        country_name: str,
        mission_goal: str,
    ) -> dict[str, int]:
        settings = get_settings()
        if not getattr(settings, "facility_ai_cleanup_enabled", True):
            return {"enabled": 0, "reviewed": 0, "excluded": 0, "websites_fixed": 0}

        # Lightweight scan only — never load contacts/sources for the whole roster at
        # once. Holding that graph open kept the DB connection checked out through the
        # LLM calls, which starved the background heartbeat and made the job look dead.
        light_rows = (
            await db.execute(
                select(RehabilitationFacility)
                .where(RehabilitationFacility.execution_id == execution_id)
                .order_by(RehabilitationFacility.canonical_name, RehabilitationFacility.id)
            )
        ).scalars().all()
        candidate_ids = [
            facility.id
            for facility in light_rows
            if normalized_publication_class(facility.publication_class) != "excluded"
            or _is_ai_reviewed(facility)
        ]
        pending_ids = [
            facility.id
            for facility in light_rows
            if facility.id in set(candidate_ids) and not _is_ai_reviewed(facility)
        ]
        already_reviewed = len(candidate_ids) - len(pending_ids)
        # Release the connection before any LLM wait so the heartbeat task can beat.
        await db.commit()

        if not candidate_ids:
            return {"enabled": 1, "reviewed": 0, "excluded": 0, "websites_fixed": 0}

        from app.services.scraping.execution_service import execution_service

        batches = 0
        if pending_ids:
            batch_size = max(
                int(getattr(settings, "facility_ai_cleanup_batch_size", DEFAULT_BATCH_SIZE) or DEFAULT_BATCH_SIZE),
                1,
            )
            model_name = getattr(settings, "facility_ai_cleanup_model", None) or getattr(
                settings, "facility_extraction_model", "gpt-4.1"
            )
            model = get_model(model_name)
            provider = get_provider_registry().get_provider(model.provider)

            for offset in range(0, len(pending_ids), batch_size):
                batch_ids = pending_ids[offset : offset + batch_size]
                batches += 1
                await execution_service.touch_heartbeat(db, execution_id)
                batch = list(
                    (
                        await db.execute(
                            select(RehabilitationFacility)
                            .where(RehabilitationFacility.id.in_(batch_ids))
                            .options(
                                selectinload(RehabilitationFacility.contacts),
                                selectinload(RehabilitationFacility.source_links).selectinload(
                                    RehabilitationFacilitySourceLink.source
                                ),
                            )
                            .order_by(
                                RehabilitationFacility.canonical_name,
                                RehabilitationFacility.id,
                            )
                        )
                    ).scalars().all()
                )
                # Snapshot everything the prompt needs before releasing the DB connection.
                facility_payloads = [_facility_payload(facility) for facility in batch]
                facility_ids = [facility.id for facility in batch]
                await db.commit()
                decisions = await self._plan_batch_with_fallback(
                    provider=provider,
                    model_slug=model.provider_model,
                    country_code=country_code,
                    country_name=country_name,
                    mission_goal=mission_goal,
                    facility_ids=facility_ids,
                    facility_payloads=facility_payloads,
                )
                writable = (
                    await db.execute(
                        select(RehabilitationFacility)
                        .where(RehabilitationFacility.id.in_(batch_ids))
                        .options(
                            selectinload(RehabilitationFacility.contacts),
                            selectinload(RehabilitationFacility.source_links).selectinload(
                                RehabilitationFacilitySourceLink.source
                            ),
                        )
                    )
                ).scalars().all()
                apply_cleanup_decisions(
                    facilities_by_id={facility.id: facility for facility in writable},
                    decisions=decisions,
                )
                await db.flush()
                await db.commit()
                await execution_service.touch_heartbeat(db, execution_id)

        # Final tallies from fresh light rows so we do not keep the heavy graph in memory.
        final_rows = (
            await db.execute(
                select(RehabilitationFacility).where(
                    RehabilitationFacility.execution_id == execution_id
                )
            )
        ).scalars().all()
        final_pool = [
            facility
            for facility in final_rows
            if normalized_publication_class(facility.publication_class) != "excluded"
            or _is_ai_reviewed(facility)
        ]
        excluded = sum(
            1
            for facility in final_pool
            if _ai_cleanup_mark(facility).get("action", "").startswith("exclude_")
        )
        websites_fixed = sum(
            1 for facility in final_pool if _ai_cleanup_mark(facility).get("website_fixed")
        )
        await db.commit()
        return {
            "enabled": 1,
            "reviewed": len(final_pool),
            "excluded": excluded,
            "websites_fixed": websites_fixed,
            "batches": batches,
            "resumed_from_prior_attempt": already_reviewed,
        }

    async def _plan_batch_with_fallback(
        self,
        *,
        provider: Any,
        model_slug: str,
        country_code: str,
        country_name: str,
        mission_goal: str,
        facility_ids: list[str],
        facility_payloads: list[dict[str, Any]],
    ) -> list[FacilityCleanupDecision]:
        """Plan a batch; on failure, fall back to solo calls so one hung prompt cannot wedge forever."""
        decisions = await self._plan_batch(
            provider=provider,
            model_slug=model_slug,
            country_code=country_code,
            country_name=country_name,
            mission_goal=mission_goal,
            facility_ids=facility_ids,
            facility_payloads=facility_payloads,
        )
        if not any(d.reason == "cleanup_batch_failed" for d in decisions):
            return decisions
        if len(facility_ids) == 1:
            # Solo already failed — mark reviewed so resume does not retry the same poison forever.
            return [
                FacilityCleanupDecision(
                    facility_id=facility_ids[0],
                    action="keep",
                    reason="cleanup_skipped_provider_error",
                )
            ]
        logger.warning(
            "facility_ai_cleanup_batch_falling_back_to_solo count=%s",
            len(facility_ids),
        )
        solo: list[FacilityCleanupDecision] = []
        for facility_id, payload in zip(facility_ids, facility_payloads, strict=True):
            solo.extend(
                await self._plan_batch_with_fallback(
                    provider=provider,
                    model_slug=model_slug,
                    country_code=country_code,
                    country_name=country_name,
                    mission_goal=mission_goal,
                    facility_ids=[facility_id],
                    facility_payloads=[payload],
                )
            )
        return solo

    async def _plan_batch(
        self,
        *,
        provider: Any,
        model_slug: str,
        country_code: str,
        country_name: str,
        mission_goal: str,
        facility_ids: list[str],
        facility_payloads: list[dict[str, Any]],
    ) -> list[FacilityCleanupDecision]:
        prompt = get_prompt_engine().render(
            "scraping/facility_cleanup.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            mission_goal=(mission_goal or "Find rehabilitation facilities.")[:2000],
            facilities_json=json.dumps(facility_payloads, ensure_ascii=True),
        )
        try:
            response = await asyncio.wait_for(
                provider.complete(
                    system=(
                        "You return strict JSON cleanup decisions for rehabilitation facility rosters. "
                        "Remove non-clinics, bad source pages, and duplicates; fix wrong websites when sure."
                    ),
                    user=prompt,
                    model=model_slug,
                    max_tokens=3500,
                ),
                timeout=BATCH_LLM_TIMEOUT_SECONDS,
            )
            raw = LLMProvider.parse_json_response(response.text)
            plan = FacilityCleanupPlan.model_validate(_normalize_cleanup_payload(raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "facility_ai_cleanup_batch_failed count=%s error=%s",
                len(facility_ids),
                exc,
            )
            return [
                FacilityCleanupDecision(facility_id=facility_id, action="keep", reason="cleanup_batch_failed")
                for facility_id in facility_ids
            ]

        by_id = {decision.facility_id: decision for decision in plan.decisions if decision.facility_id}
        # Ensure every facility has a decision; default keep on omission.
        result: list[FacilityCleanupDecision] = []
        for facility_id in facility_ids:
            result.append(
                by_id.get(facility_id)
                or FacilityCleanupDecision(facility_id=facility_id, action="keep", reason="missing_decision")
            )
        return result


def apply_cleanup_decisions(
    *,
    facilities_by_id: dict[str, RehabilitationFacility],
    decisions: list[FacilityCleanupDecision],
) -> dict[str, int]:
    excluded = 0
    websites_fixed = 0
    seen: set[str] = set()

    for decision in decisions:
        facility = facilities_by_id.get(decision.facility_id)
        if facility is None or decision.facility_id in seen:
            continue
        seen.add(decision.facility_id)
        reason = (decision.reason or decision.action)[:MAX_REASON_LEN]

        if decision.action == "keep":
            if decision.reason == "cleanup_batch_failed":
                # Provider/parse error for this batch — leave unreviewed so a future
                # attempt retries it instead of permanently skipping on a transient failure.
                continue
            website_fixed = False
            if decision.corrected_website and _apply_website(facility, decision.corrected_website):
                website_fixed = True
                websites_fixed += 1
            _record_cleanup(facility, action="keep", reason=reason, website_fixed=website_fixed)
            continue

        if decision.action == "exclude_duplicate":
            keep_id = decision.keep_facility_id
            if not keep_id or keep_id == facility.id or keep_id not in facilities_by_id:
                # Invalid duplicate target — do not exclude.
                continue
            facility.publication_class = "excluded"
            facility.duplicate_status = "merged"
            facility.human_review_status = "not_required"
            _record_cleanup(
                facility,
                action="exclude_duplicate",
                reason=reason,
                keep_facility_id=keep_id,
            )
            excluded += 1
            continue

        if decision.action in {"exclude_not_rehab", "exclude_bad_source"}:
            facility.publication_class = "excluded"
            facility.human_review_status = "not_required"
            _record_cleanup(facility, action=decision.action, reason=reason)
            excluded += 1
            if decision.corrected_website and decision.action == "exclude_bad_source":
                # Still drop the row; do not keep a bad listing just because a better URL is known.
                pass

    return {"excluded": excluded, "websites_fixed": websites_fixed}


def _facility_payload(facility: RehabilitationFacility) -> dict[str, Any]:
    contacts = list(facility.contacts or [])
    phones = [
        contact.value
        for contact in contacts
        if str(contact.contact_type or "").lower() in {"phone", "hotline", "whatsapp"} and contact.value
    ][:3]
    emails = [
        contact.value
        for contact in contacts
        if str(contact.contact_type or "").lower() == "email" and contact.value
    ][:2]
    websites = []
    if facility.primary_website:
        websites.append(facility.primary_website)
    for contact in contacts:
        if str(contact.contact_type or "").lower() in {"website", "booking_url"} and contact.value:
            if contact.value not in websites:
                websites.append(contact.value)
    source_urls: list[str] = []
    for link in list(getattr(facility, "source_links", None) or [])[:4]:
        source = getattr(link, "source", None)
        url = getattr(source, "url", None) or getattr(link, "source_url", None)
        if url and url not in source_urls:
            source_urls.append(str(url)[:512])
    return {
        "facility_id": facility.id,
        "name": facility.canonical_name,
        "facility_type": facility.facility_type,
        "city": facility.primary_city,
        "region": facility.primary_region,
        "address": facility.primary_address,
        "websites": websites[:4],
        "phones": phones,
        "emails": emails,
        "source_urls": source_urls[:4],
        "publication_class": facility.publication_class,
    }


def _normalize_cleanup_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("decisions") or raw.get("facilities") or raw.get("results") or []
    else:
        items = []
    if not isinstance(items, list):
        items = []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "facility_id": item.get("facility_id") or item.get("id"),
                "action": item.get("action") or item.get("decision") or "keep",
                "keep_facility_id": item.get("keep_facility_id") or item.get("canonical_facility_id"),
                "corrected_website": item.get("corrected_website")
                or item.get("website")
                or item.get("fixed_website"),
                "reason": item.get("reason") or item.get("explanation") or "",
            }
        )
    return {"decisions": normalized}


def _apply_website(facility: RehabilitationFacility, raw_url: str) -> bool:
    try:
        canonical = canonicalize_discovery_url(raw_url)
    except UrlRejected:
        return False
    url = canonical.canonical_url
    if not _safe_http_url(url):
        return False
    if (facility.primary_website or "").strip() == url:
        return False
    facility.primary_website = url[:512]
    # Refresh primary website contact when present.
    for contact in list(facility.contacts or []):
        if str(contact.contact_type or "").lower() in {"website", "booking_url"} and contact.is_primary:
            contact.value = url[:512]
            contact.normalized_value = url[:512]
            return True
    facility.contacts.append(
        RehabilitationFacilityContact(
            facility_id=facility.id,
            location_id=None,
            contact_type="website",
            label="Website",
            value=url[:512],
            normalized_value=url[:512],
            is_primary=True,
            verification_status="unverified",
            confidence_score=0.7,
            contact_discovery_status="ai_cleanup_corrected",
            is_mock=facility.is_mock,
        )
    )
    return True


def _record_cleanup(
    facility: RehabilitationFacility,
    *,
    action: str,
    reason: str,
    keep_facility_id: str | None = None,
    website_fixed: bool = False,
) -> None:
    payload = dict(facility.hard_gate_results_json or {})
    payload["ai_cleanup"] = {
        "action": action,
        "reason": reason,
        "keep_facility_id": keep_facility_id,
        "website_fixed": website_fixed,
    }
    facility.hard_gate_results_json = payload
    if action.startswith("exclude_"):
        facility.country_containment_reason = f"AI cleanup: {reason}"[:500]


def _ai_cleanup_mark(facility: RehabilitationFacility) -> dict[str, Any]:
    payload = facility.hard_gate_results_json or {}
    mark = payload.get("ai_cleanup")
    return mark if isinstance(mark, dict) else {}


def _is_ai_reviewed(facility: RehabilitationFacility) -> bool:
    return bool(_ai_cleanup_mark(facility))


def _safe_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


facility_ai_cleanup_service = FacilityAiCleanupService()
