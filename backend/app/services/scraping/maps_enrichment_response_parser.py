"""Resilient parsing for Maps enrichment Sonar/LLM JSON responses."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass

from pydantic import ValidationError

logger = logging.getLogger(__name__)

RAW_RESPONSE_LOG_MAX_CHARS = 2000
FENCE_PATTERN = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)
FENCE_END_PATTERN = re.compile(r"\n?```\s*$")


@dataclass
class EnrichmentParseStats:
    parse_failures: int = 0
    repair_attempts: int = 0
    repair_successes: int = 0
    final_failed: int = 0

    def merge(self, other: EnrichmentParseStats) -> EnrichmentParseStats:
        return EnrichmentParseStats(
            parse_failures=self.parse_failures + other.parse_failures,
            repair_attempts=self.repair_attempts + other.repair_attempts,
            repair_successes=self.repair_successes + other.repair_successes,
            final_failed=self.final_failed + other.final_failed,
        )

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


class EnrichmentParseError(ValueError):
    def __init__(self, message: str, *, raw_text: str = "") -> None:
        super().__init__(message)
        self.raw_text = raw_text


def truncate_for_log(text: str, *, max_chars: int = RAW_RESPONSE_LOG_MAX_CHARS) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    suffix = "…[truncated]"
    keep = max(0, max_chars - len(suffix))
    return cleaned[:keep] + suffix


def prepare_response_text(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = FENCE_PATTERN.sub("", cleaned)
        cleaned = FENCE_END_PATTERN.sub("", cleaned).strip()
    brace = cleaned.find("{")
    bracket = cleaned.find("[")
    starts = [index for index in (brace, bracket) if index >= 0]
    if starts:
        cleaned = cleaned[min(starts) :].strip()
    return cleaned


def extract_first_json_value(text: str) -> str:
    prepared = prepare_response_text(text)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = prepared.find(opener)
        if start < 0:
            continue
        snippet = _extract_balanced_json(prepared, start, opener, closer)
        if snippet is not None:
            return snippet
    raise EnrichmentParseError("no JSON object found", raw_text=text)


def _extract_balanced_json(text: str, start: int, opener: str, closer: str) -> str | None:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    if in_string or depth > 0:
        raise EnrichmentParseError("unterminated JSON value", raw_text=text)
    return None


def parse_enrichment_response_text(text: str) -> dict:
    snippet = extract_first_json_value(text)
    try:
        parsed = json.loads(snippet)
    except json.JSONDecodeError as exc:
        raise EnrichmentParseError(str(exc), raw_text=text) from exc
    if not isinstance(parsed, (dict, list)):
        raise EnrichmentParseError("JSON root must be an object or array", raw_text=text)
    return parsed if isinstance(parsed, dict) else {"results": parsed}


def validate_enrichment_batch(raw: dict):
    from app.services.scraping.maps_place_enrichment_service import (
        MapsPlaceEnrichmentBatch,
        _normalize_batch_payload,
    )

    normalized = _normalize_batch_payload(raw)
    return MapsPlaceEnrichmentBatch.model_validate(normalized)


def parse_and_validate_enrichment_response(text: str):
    raw = parse_enrichment_response_text(text)
    return validate_enrichment_batch(raw)


def log_parse_failure(*, attempt: str, error: Exception, raw_text: str) -> None:
    logger.warning(
        "maps_enrichment_parse_failed attempt=%s error=%s raw=%s",
        attempt,
        error,
        truncate_for_log(raw_text),
    )


def record_parse_failure(stats: EnrichmentParseStats, *, error: Exception, raw_text: str, attempt: str) -> None:
    stats.parse_failures += 1
    log_parse_failure(attempt=attempt, error=error, raw_text=raw_text)


def record_schema_failure(stats: EnrichmentParseStats, *, error: ValidationError, raw_text: str) -> None:
    stats.parse_failures += 1
    log_parse_failure(attempt="schema_validation", error=error, raw_text=raw_text)


ENRICHMENT_JSON_SCHEMA_HINT = """
Return a JSON object with this shape only:
{
  "results": [
    {
      "place_id": "string",
      "operator_type": "string",
      "ownership_status": "string",
      "funding_type": "string",
      "facility_type": "string",
      "care_setting": "string",
      "organization_scope": "string",
      "addiction_focus_confirmed": true,
      "medical_detox": false,
      "residential_accommodation": true,
      "operating_status": "open",
      "classification_evidence": {},
      "classification_confidence": 0.0,
      "addictions_treated": [],
      "languages_spoken": [],
      "treatment_price": "string or null",
      "email": "string or null",
      "phone": "string or null",
      "bed_count": "integer or null"
    }
  ]
}
""".strip()
