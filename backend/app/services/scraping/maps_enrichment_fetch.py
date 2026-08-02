"""Resilient LLM fetch + parse/repair loop for Maps enrichment batches."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import ValidationError

from app.llm.prompt_engine import get_prompt_engine
from app.services.scraping.maps_enrichment_response_parser import (
    ENRICHMENT_JSON_SCHEMA_HINT,
    EnrichmentParseError,
    EnrichmentParseStats,
    parse_and_validate_enrichment_response,
    record_parse_failure,
    record_schema_failure,
    truncate_for_log,
)

ENRICHMENT_TIMEOUT_SECONDS = 120.0

logger = logging.getLogger(__name__)

JSON_ONLY_SYSTEM = (
    "You have live web search. Research each addiction-rehab facility independently. "
    "Return strict JSON only — no markdown fences, no commentary, no trailing prose."
)
DEFAULT_SYSTEM = (
    "You have live web search. Research each addiction-rehab facility independently "
    "and return strict JSON. Never invent values without a supporting web source."
)
RETRY_TEMPERATURE = 0.1
DEFAULT_TEMPERATURE = 0.7


class EnrichmentFetchError(Exception):
    def __init__(
        self,
        message: str,
        *,
        raw_excerpt: str = "",
        stats: EnrichmentParseStats | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_excerpt = raw_excerpt
        self.stats = stats or EnrichmentParseStats()


def reduce_payloads_for_retry(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reduced: list[dict[str, Any]] = []
    for payload in payloads:
        item = dict(payload)
        excerpt = item.pop("website_crawl_excerpt", None)
        if excerpt:
            item["website_crawl_excerpt"] = str(excerpt)[:600]
        reduced.append(item)
    return reduced


def cap_payload_excerpt(payload: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    item = dict(payload)
    excerpt = item.get("website_crawl_excerpt")
    if excerpt and len(str(excerpt)) > max_chars:
        item["website_crawl_excerpt"] = str(excerpt)[:max_chars]
    return item


def _render_prompt(
    *,
    country_code: str,
    country_name: str,
    payloads: list[dict[str, Any]],
) -> str:
    from app.services.scraping.maps_place_enrichment_service import ADDICTION_TAXONOMY

    return get_prompt_engine().render(
        "scraping/maps_place_enricher.j2",
        country_code=(country_code or "XX")[:2].upper(),
        country_name=(country_name or "Unknown")[:120],
        addiction_taxonomy_json=json.dumps(list(ADDICTION_TAXONOMY), ensure_ascii=True),
        facilities_json=json.dumps(payloads, ensure_ascii=False),
    )


def _repair_prompt(*, malformed_response: str) -> str:
    return (
        "The previous response was malformed JSON and could not be parsed.\n"
        "Fix it and return JSON only.\n\n"
        f"Required schema:\n{ENRICHMENT_JSON_SCHEMA_HINT}\n\n"
        f"Malformed response:\n{truncate_for_log(malformed_response)}\n"
    )


async def _complete(
    provider: Any,
    *,
    system: str,
    user: str,
    model_slug: str,
    max_tokens: int,
    temperature: float | None = None,
) -> str:
    response = await asyncio.wait_for(
        provider.complete(
            system=system,
            user=user,
            model=model_slug,
            max_tokens=max_tokens,
            temperature=temperature,
        ),
        timeout=ENRICHMENT_TIMEOUT_SECONDS,
    )
    return response.text or ""


def _parse_response(text: str, stats: EnrichmentParseStats, *, attempt: str):
    try:
        batch = parse_and_validate_enrichment_response(text)
        return batch.results
    except EnrichmentParseError as exc:
        record_parse_failure(stats, error=exc, raw_text=exc.raw_text or text, attempt=attempt)
        raise
    except ValidationError as exc:
        record_schema_failure(stats, error=exc, raw_text=text)
        raise EnrichmentParseError(str(exc), raw_text=text) from exc


async def fetch_enrichment_batch(
    provider: Any,
    *,
    model_slug: str,
    country_code: str,
    country_name: str,
    payloads: list[dict[str, Any]],
    stats: EnrichmentParseStats,
    max_tokens: int = 3000,
) -> list:
    prompt = _render_prompt(
        country_code=country_code,
        country_name=country_name,
        payloads=payloads,
    )
    last_raw = ""
    last_error: Exception | None = None

    try:
        last_raw = await _complete(
            provider,
            system=DEFAULT_SYSTEM,
            user=prompt,
            model_slug=model_slug,
            max_tokens=max_tokens,
            temperature=DEFAULT_TEMPERATURE,
        )
        return _parse_response(last_raw, stats, attempt="initial")
    except (EnrichmentParseError, ValidationError) as exc:
        last_error = exc
        logger.info("maps_enrichment_initial_parse_failed; attempting repair")

    stats.repair_attempts += 1
    try:
        repair_text = await _complete(
            provider,
            system=JSON_ONLY_SYSTEM,
            user=_repair_prompt(malformed_response=last_raw),
            model_slug=model_slug,
            max_tokens=max_tokens,
            temperature=RETRY_TEMPERATURE,
        )
        results = _parse_response(repair_text, stats, attempt="repair")
        stats.repair_successes += 1
        return results
    except (EnrichmentParseError, ValidationError) as exc:
        last_error = exc
        logger.info("maps_enrichment_repair_failed; attempting reduced retry")

    reduced_payloads = reduce_payloads_for_retry(payloads)
    retry_prompt = _render_prompt(
        country_code=country_code,
        country_name=country_name,
        payloads=reduced_payloads,
    )
    try:
        retry_raw = await _complete(
            provider,
            system=JSON_ONLY_SYSTEM,
            user=retry_prompt + "\n\nReturn JSON only.",
            model_slug=model_slug,
            max_tokens=max_tokens,
            temperature=RETRY_TEMPERATURE,
        )
        return _parse_response(retry_raw, stats, attempt="reduced_retry")
    except (EnrichmentParseError, ValidationError) as exc:
        last_error = exc
        last_raw = getattr(exc, "raw_text", "") or last_raw

    stats.final_failed += len(payloads)
    message = str(last_error or "enrichment response parse failed")
    raw_excerpt = truncate_for_log(last_raw or getattr(last_error, "raw_text", ""))
    logger.warning(
        "maps_enrichment_fetch_exhausted count=%s error=%s raw=%s",
        len(payloads),
        message,
        raw_excerpt,
    )
    raise EnrichmentFetchError(message, raw_excerpt=raw_excerpt, stats=stats) from last_error
