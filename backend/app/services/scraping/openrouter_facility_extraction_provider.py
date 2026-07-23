"""OpenRouter implementation for structured facility extraction."""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError

from app.core.config import get_settings
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry
from app.services.scraping.facility_extraction_provider import (
    EXTRACTION_SCHEMA_VERSION,
    FacilityExtractionOutput,
    FacilityExtractionProvider,
    FacilityExtractionProviderResult,
    FacilityStructuredOutputError,
)

PROMPT_VERSION = "facility-extractor-v2"
MAX_REPAIR_INPUT_CHARS = 20_000
MAX_DIAGNOSTIC_ITEMS = 8


class OpenRouterFacilityExtractionProvider(FacilityExtractionProvider):
    provider_name = "openrouter"
    prompt_version = PROMPT_VERSION
    schema_version = EXTRACTION_SCHEMA_VERSION

    def __init__(self, *, model_id: str | None = None) -> None:
        self.model_id = model_id or get_settings().facility_extraction_model
        self._model_entry = get_model(self.model_id)
        self.model = self._model_entry.provider_model
        self._provider = get_provider_registry().get_provider(self._model_entry.provider)

    async def extract(
        self, *, chunk_text: str, language_hint: str | None = None
    ) -> FacilityExtractionProviderResult:
        prompt = _render_extraction_prompt(chunk_text, language_hint)
        response_format = _response_format()
        try:
            response = await self._provider.complete(
                system=_system_prompt(),
                user=prompt,
                model=self.model,
                max_tokens=get_settings().facility_extraction_max_output_tokens,
                response_format=response_format,
            )
            provider_request_id = _provider_request_id(response.raw)
            output, diagnostics = _parse_validate(
                response.text,
                response_format_requested=True,
                repair_attempted=False,
            )
            return FacilityExtractionProviderResult(
                output=output,
                diagnostics=diagnostics,
                provider_request_id=provider_request_id,
            )
        except StructuredParseValidationError as exc:
            provider_request_id = _provider_request_id(getattr(locals().get("response", None), "raw", None))
            if not exc.repairable:
                raise FacilityStructuredOutputError(
                    "Facility extraction returned invalid structured output",
                    exc.diagnostics,
                ) from exc
            repaired = await self._repair_once(
                invalid_output=getattr(locals().get("response", None), "text", ""),
                diagnostics=exc.diagnostics,
            )
            try:
                output, repair_diagnostics = _parse_validate(
                    repaired.text,
                    response_format_requested=True,
                    repair_attempted=True,
                    parse_stage_prefix="repair_",
                )
            except StructuredParseValidationError as repair_exc:
                diagnostics = {
                    **exc.diagnostics,
                    "repair_attempted": True,
                    "repair_failed": True,
                    "repair": repair_exc.diagnostics,
                }
                raise FacilityStructuredOutputError(
                    "Facility extraction returned invalid structured output",
                    diagnostics,
                ) from repair_exc
            return FacilityExtractionProviderResult(
                output=output,
                diagnostics={**repair_diagnostics, "repair_attempted": True},
                provider_request_id=provider_request_id or _provider_request_id(repaired.raw),
            )
        except httpx.TimeoutException as exc:
            raise FacilityProviderError("timeout", "Provider request timed out", retryable=True) from exc
        except httpx.ConnectError as exc:
            raise FacilityProviderError("connection_failed", "Provider connection failed", retryable=True) from exc
        except RuntimeError as exc:
            message = str(exc).lower()
            if "429" in message or "rate" in message:
                raise FacilityProviderError("provider_rate_limited", "Provider rate limited", retryable=True) from exc
            if "500" in message or "502" in message or "503" in message or "504" in message:
                raise FacilityProviderError(
                    "temporary_provider_error",
                    "Temporary provider error",
                    retryable=True,
                ) from exc
            raise FacilityProviderError("provider_error", "Provider extraction failed", retryable=False) from exc

    async def _repair_once(self, *, invalid_output: Any, diagnostics: dict[str, Any]):
        repair_user = (
            "The previous facility extraction output was invalid.\n"
            "Repair it into valid JSON matching the schema. Do not add Markdown. "
            "Do not invent facilities or evidence. If unsupported, return an empty facilities array.\n\n"
            f"Validation diagnostics:\n{json.dumps(diagnostics, ensure_ascii=True)[:4000]}\n\n"
            "Invalid output:\n"
            f"{_bounded_text(invalid_output, MAX_REPAIR_INPUT_CHARS)}"
        )
        return await self._provider.complete(
            system=_system_prompt(),
            user=repair_user,
            model=self.model,
            max_tokens=get_settings().facility_extraction_max_output_tokens,
            response_format=_response_format(),
        )


class StructuredParseValidationError(Exception):
    def __init__(self, diagnostics: dict[str, Any], *, repairable: bool) -> None:
        super().__init__("invalid structured output")
        self.diagnostics = diagnostics
        self.repairable = repairable


class FacilityProviderError(Exception):
    def __init__(self, classification: str, safe_message: str, *, retryable: bool) -> None:
        super().__init__(safe_message)
        self.classification = classification
        self.safe_message = safe_message
        self.retryable = retryable


def _render_extraction_prompt(chunk_text: str, language_hint: str | None) -> str:
    return get_prompt_engine().render(
        "scraping/facility_extractor.j2",
        chunk_text=chunk_text,
        language_hint=language_hint or "",
        max_candidates=get_settings().facility_extraction_max_candidates_per_chunk,
        max_quote_characters=get_settings().facility_extraction_max_evidence_quote_characters,
    )


def _system_prompt() -> str:
    return (
        "You extract rehabilitation/addiction-treatment facilities from untrusted source text "
        "and return strict JSON only."
    )


def _response_format() -> dict[str, Any]:
    schema = _strict_json_schema(FacilityExtractionOutput.model_json_schema())
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "facility_extraction_output",
            "strict": True,
            "schema": schema,
        },
    }


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(schema))
    _require_object_properties(copied)
    return copied


def _require_object_properties(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["additionalProperties"] = False
                node["required"] = sorted(properties)
        for value in node.values():
            _require_object_properties(value)
    elif isinstance(node, list):
        for value in node:
            _require_object_properties(value)


def _parse_validate(
    raw_content: Any,
    *,
    response_format_requested: bool,
    repair_attempted: bool,
    parse_stage_prefix: str = "",
) -> tuple[FacilityExtractionOutput, dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "parse_stage": f"{parse_stage_prefix}response_unwrap",
        "response_format_requested": response_format_requested,
        "markdown_fence_present": False,
        "repair_attempted": repair_attempted,
    }
    try:
        parsed, fence_present = _coerce_json_object(raw_content)
    except json.JSONDecodeError as exc:
        diagnostics.update(
            {
                "parse_stage": f"{parse_stage_prefix}json_decode",
                "json_error": exc.__class__.__name__,
            }
        )
        raise StructuredParseValidationError(diagnostics, repairable=True) from exc
    except ValueError as exc:
        diagnostics.update(
            {
                "parse_stage": f"{parse_stage_prefix}json_decode",
                "json_error": type(exc).__name__,
            }
        )
        raise StructuredParseValidationError(diagnostics, repairable=False) from exc

    diagnostics["markdown_fence_present"] = fence_present
    try:
        output = FacilityExtractionOutput.model_validate(parsed)
    except PydanticValidationError as exc:
        diagnostics.update(
            {
                "parse_stage": f"{parse_stage_prefix}schema_validation",
                "validation_error_count": len(exc.errors()),
                "validation_errors": _safe_pydantic_errors(exc),
            }
        )
        raise StructuredParseValidationError(
            diagnostics,
            repairable=_is_repairable_schema_error(exc),
        ) from exc

    diagnostics.update(
        {
            "parse_stage": f"{parse_stage_prefix}schema_validation",
            "validation_error_count": 0,
            "facility_count": len(output.facilities),
        }
    )
    return output, diagnostics


def _coerce_json_object(raw_content: Any) -> tuple[dict[str, Any], bool]:
    if isinstance(raw_content, dict):
        return raw_content, False
    if not isinstance(raw_content, str):
        raise ValueError("unsupported_response_content_type")
    text = raw_content.strip()
    fence_present = _is_single_json_fence(text)
    if fence_present:
        text = _strip_single_json_fence(text)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("json_root_not_object")
    return parsed, fence_present


def _is_single_json_fence(text: str) -> bool:
    if not text.startswith("```"):
        return False
    if not text.endswith("```"):
        raise ValueError("partial_markdown_fence")
    first_line, _, remainder = text.partition("\n")
    if first_line not in {"```json", "```"}:
        raise ValueError("unsupported_markdown_fence")
    if "```" in remainder[:-3]:
        raise ValueError("multiple_markdown_fences")
    return True


def _strip_single_json_fence(text: str) -> str:
    _, _, remainder = text.partition("\n")
    return remainder.removesuffix("```").strip()


def _safe_pydantic_errors(exc: PydanticValidationError) -> list[dict[str, str]]:
    safe: list[dict[str, str]] = []
    for error in exc.errors()[:MAX_DIAGNOSTIC_ITEMS]:
        loc = ".".join(str(part) for part in error.get("loc", ()))[:160]
        safe.append({"loc": loc, "type": str(error.get("type", ""))[:80]})
    return safe


def _is_repairable_schema_error(exc: PydanticValidationError) -> bool:
    errors = exc.errors()
    if not errors:
        return False
    top_level_missing = {"document_relevant", "facilities"}
    return all(
        error.get("type") == "missing"
        and len(error.get("loc", ())) == 1
        and error.get("loc", (None,))[0] in top_level_missing
        for error in errors
    )


def _provider_request_id(raw: dict[str, Any] | None) -> str | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get("id")
    return str(value)[:255] if value is not None else None


def _bounded_text(value: Any, limit: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=True)
        except TypeError:
            text = str(value)
    return text[:limit]
