"""Phase 5C-E retrieval boundaries and deterministic fallback policy.

All provider calls are injected or made by explicit production adapters. Database
transactions are intentionally absent from this module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Settings, get_settings
from app.services.scraping.discovery_url_service import canonicalize_discovery_target
from app.services.scraping.phase5_contracts import Phase5WorkKind
from app.services.scraping.source_retrieval_service import (
    SourceRetrievalError,
    SourceRetrievalService,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievalFailureCategory(str, Enum):
    MISSING_CONFIGURATION = "missing_configuration"
    AUTHENTICATION = "authentication_failure"
    CREDIT_EXHAUSTED = "credit_exhausted"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    RETRYABLE_PROVIDER = "retryable_provider_failure"
    INVALID_RESPONSE = "invalid_provider_response"
    PROVIDER_OUTAGE = "provider_outage"
    UNSAFE_URL = "unsafe_returned_url"
    MALFORMED_CONTENT = "malformed_content"
    RESPONSE_TOO_LARGE = "response_too_large"
    REDIRECT_LOOP = "redirect_loop"
    HTTP_ERROR = "http_error"


class BrowserActionType(str, Enum):
    NAVIGATE = "navigate"
    PAGINATE = "paginate"
    LOAD_MORE = "load_more"
    INFINITE_SCROLL = "infinite_scroll"
    MAP_INTERACTION = "map_interaction"


class PreparedResource(StrictModel):
    requested_url: str
    final_url: str
    canonical_resource_url: str
    content_type: str
    body: bytes = Field(repr=False)
    content_length: int = Field(ge=0)
    content_sha256: str = Field(min_length=64, max_length=64)
    response_fingerprint: str = Field(min_length=64, max_length=64)
    redirect_count: int = Field(default=0, ge=0)
    retrieval_method: Phase5WorkKind
    resource_role: str = "page"
    result_ordinal: int = Field(default=0, ge=0)
    requested_at: datetime
    fetched_at: datetime
    provider_request_id: str | None = None
    provider_result_status: str | None = None
    action_type: BrowserActionType | None = None
    continuation_state: dict[str, Any] = {}

    @field_validator("content_sha256", "response_fingerprint")
    @classmethod
    def hashes_are_hex(cls, value: str) -> str:
        int(value, 16)
        return value


class RetrievalActionResult(StrictModel):
    outcome: str
    resources: tuple[PreparedResource, ...] = ()
    failure_category: RetrievalFailureCategory | None = None
    public_message: str | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0)
    provider_wide_blocker: bool = False


class FirecrawlTransport(Protocol):
    async def scrape(self, *, url: str) -> dict[str, Any]: ...


class BrowserTransport(Protocol):
    async def perform(
        self, *, url: str, action_type: BrowserActionType,
        continuation_state: dict[str, Any],
    ) -> dict[str, Any]: ...


def prepare_resource(
    *, requested_url: str, final_url: str, content_type: str, body: bytes,
    retrieval_method: Phase5WorkKind, requested_at: datetime, fetched_at: datetime,
    redirect_count: int = 0, resource_role: str = "page", result_ordinal: int = 0,
    provider_request_id: str | None = None, provider_result_status: str | None = None,
    action_type: BrowserActionType | None = None,
    continuation_state: dict[str, Any] | None = None,
) -> PreparedResource:
    target = canonicalize_discovery_target(final_url)
    if not target.is_valid or not target.is_statically_safe:
        raise ValueError("retrieval returned an unsafe resource URL")
    digest = hashlib.sha256(body).hexdigest()
    fingerprint_payload = json.dumps({
        "schema": "phase5_resource_v1",
        "method": retrieval_method.value,
        "canonical_url": target.canonical_url,
        "content_sha256": digest,
        "resource_role": resource_role,
        "result_ordinal": result_ordinal,
    }, sort_keys=True, separators=(",", ":")).encode()
    return PreparedResource(
        requested_url=requested_url, final_url=final_url,
        canonical_resource_url=target.canonical_url, content_type=content_type,
        body=body, content_length=len(body), content_sha256=digest,
        response_fingerprint=hashlib.sha256(fingerprint_payload).hexdigest(),
        redirect_count=redirect_count, retrieval_method=retrieval_method,
        resource_role=resource_role, result_ordinal=result_ordinal,
        requested_at=requested_at, fetched_at=fetched_at,
        provider_request_id=provider_request_id,
        provider_result_status=provider_result_status, action_type=action_type,
        continuation_state=continuation_state or {})


class NormalHttpRetriever:
    """Use the existing SSRF-hardened, bounded HTTP implementation without a DB."""

    def __init__(self, boundary: SourceRetrievalService | None = None):
        self.boundary = boundary or SourceRetrievalService()

    async def retrieve(
        self, *, url: str, requested_at: datetime, fetched_at: datetime,
    ) -> RetrievalActionResult:
        try:
            validated = await self.boundary._validate_url(url)
            fetched = await self.boundary._fetch(validated)
        except SourceRetrievalError as exc:
            category = _http_failure_category(exc.status.value)
            return RetrievalActionResult(
                outcome="retryable_failure" if category in {
                    RetrievalFailureCategory.TIMEOUT,
                    RetrievalFailureCategory.RATE_LIMIT,
                    RetrievalFailureCategory.PROVIDER_OUTAGE,
                } else "terminal_failure",
                failure_category=category,
                public_message=_safe_failure_message(category))
        content_type = (fetched.content_type or "application/octet-stream").split(";", 1)[0]
        try:
            resource = prepare_resource(
                requested_url=url, final_url=fetched.final_url,
                content_type=content_type, body=fetched.content,
                retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL,
                redirect_count=fetched.redirect_count,
                requested_at=requested_at, fetched_at=fetched_at)
        except ValueError:
            return RetrievalActionResult(
                outcome="terminal_failure",
                failure_category=RetrievalFailureCategory.UNSAFE_URL,
                public_message=_safe_failure_message(RetrievalFailureCategory.UNSAFE_URL))
        return RetrievalActionResult(outcome="succeeded", resources=(resource,))


class FirecrawlRetriever:
    def __init__(self, transport: FirecrawlTransport | None = None,
                 settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.transport = transport

    async def retrieve(
        self, *, url: str, requested_at: datetime, fetched_at: datetime,
    ) -> RetrievalActionResult:
        if self.transport is None and not self.settings.firecrawl_api_key:
            return RetrievalActionResult(
                outcome="blocked",
                failure_category=RetrievalFailureCategory.MISSING_CONFIGURATION,
                public_message="Firecrawl is not configured.",
                provider_wide_blocker=True)
        transport = self.transport or _ProductionFirecrawlTransport(self.settings)
        try:
            payload = await transport.scrape(url=url)
        except httpx.TimeoutException:
            return _provider_failure(RetrievalFailureCategory.TIMEOUT)
        except httpx.HTTPStatusError as exc:
            return _firecrawl_http_failure(exc.response.status_code)
        except httpx.HTTPError:
            return _provider_failure(RetrievalFailureCategory.PROVIDER_OUTAGE)
        if not isinstance(payload, dict):
            return _provider_failure(RetrievalFailureCategory.INVALID_RESPONSE, retryable=False)
        raw_resources = payload.get("resources")
        if raw_resources is None:
            raw_resources = [payload.get("data", payload)]
        if not isinstance(raw_resources, list):
            return _provider_failure(RetrievalFailureCategory.INVALID_RESPONSE, retryable=False)
        resources = []
        for ordinal, item in enumerate(raw_resources):
            if not isinstance(item, dict):
                continue
            final_url = item.get("url") or item.get("sourceURL") or url
            content = item.get("html") or item.get("markdown") or item.get("content")
            if not isinstance(final_url, str) or not isinstance(content, str):
                continue
            try:
                resources.append(prepare_resource(
                    requested_url=url, final_url=final_url,
                    content_type=item.get("contentType") or (
                        "text/html" if item.get("html") else "text/markdown"),
                    body=content.encode("utf-8"),
                    retrieval_method=Phase5WorkKind.FIRECRAWL_RETRIEVAL,
                    requested_at=requested_at, fetched_at=fetched_at,
                    resource_role=str(item.get("role") or "page")[:64],
                    result_ordinal=ordinal,
                    provider_request_id=_safe_identifier(payload.get("id")),
                    provider_result_status=_safe_identifier(payload.get("status"))))
            except ValueError:
                continue
        if not resources:
            return _provider_failure(RetrievalFailureCategory.INVALID_RESPONSE, retryable=False)
        return RetrievalActionResult(outcome="succeeded", resources=tuple(resources))


class PlaywrightRetriever:
    def __init__(self, transport: BrowserTransport | None = None,
                 settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.transport = transport

    async def retrieve(
        self, *, url: str, action_type: BrowserActionType,
        continuation_state: dict[str, Any], requested_at: datetime,
        fetched_at: datetime,
    ) -> RetrievalActionResult:
        if self.transport is None and not self.settings.playwright_enabled:
            return RetrievalActionResult(
                outcome="blocked",
                failure_category=RetrievalFailureCategory.MISSING_CONFIGURATION,
                public_message="Playwright browser transport is unavailable.",
                provider_wide_blocker=True)
        transport = self.transport or _ProductionPlaywrightTransport(self.settings)
        prior = continuation_state.get("state_fingerprint")
        try:
            result = await transport.perform(
                url=url, action_type=action_type,
                continuation_state=dict(continuation_state))
        except TimeoutError:
            return _provider_failure(RetrievalFailureCategory.TIMEOUT)
        except Exception:
            return _provider_failure(RetrievalFailureCategory.RETRYABLE_PROVIDER)
        if not isinstance(result, dict) or not isinstance(result.get("content"), str):
            return _provider_failure(RetrievalFailureCategory.INVALID_RESPONSE, retryable=False)
        safe_state = _safe_continuation_state(result.get("continuation_state"))
        state_fingerprint = hashlib.sha256(json.dumps(
            safe_state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if prior and prior == state_fingerprint:
            return RetrievalActionResult(
                outcome="terminal_failure",
                failure_category=RetrievalFailureCategory.REDIRECT_LOOP,
                public_message="Browser continuation state repeated.")
        safe_state["state_fingerprint"] = state_fingerprint
        try:
            resource = prepare_resource(
                requested_url=url, final_url=result.get("url") or url,
                content_type=result.get("content_type") or "text/html",
                body=result["content"].encode(),
                retrieval_method=Phase5WorkKind.PLAYWRIGHT_RETRIEVAL,
                requested_at=requested_at, fetched_at=fetched_at,
                action_type=action_type, continuation_state=safe_state)
        except ValueError:
            return _provider_failure(RetrievalFailureCategory.UNSAFE_URL, retryable=False)
        return RetrievalActionResult(outcome="succeeded", resources=(resource,))


def fallback_work_kind(expansion_outcome: str) -> Phase5WorkKind | None:
    if expansion_outcome in {
        "requires_managed_rendering", "unsupported_content_representation",
    }:
        return Phase5WorkKind.FIRECRAWL_RETRIEVAL
    if expansion_outcome == "requires_browser_interaction":
        return Phase5WorkKind.PLAYWRIGHT_RETRIEVAL
    return None


class _ProductionFirecrawlTransport:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def scrape(self, *, url: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.settings.firecrawl_api_key}"}
        timeout = httpx.Timeout(self.settings.firecrawl_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            response = await client.post(
                f"{self.settings.firecrawl_base_url.rstrip('/')}/scrape",
                json={"url": url, "formats": ["markdown", "html"]})
            response.raise_for_status()
            return response.json()


class _ProductionPlaywrightTransport:
    """Minimal browser boundary; enabled explicitly and imports Playwright lazily."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.network_guard = SourceRetrievalService()

    async def perform(self, *, url: str, action_type: BrowserActionType,
                      continuation_state: dict[str, Any]) -> dict[str, Any]:
        del continuation_state
        await self.network_guard._validate_url(url)
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright runtime is unavailable") from exc
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=self.settings.source_retrieval_user_agent)
            page = await context.new_page()
            page.set_default_timeout(
                self.settings.playwright_navigation_timeout_seconds * 1000)
            await page.goto(url, wait_until="domcontentloaded")
            if action_type is BrowserActionType.PAGINATE:
                target = page.locator(
                    "a[rel='next'], .pagination a.next, [aria-label='Next']").first
                if await target.count():
                    await target.click()
                    await page.wait_for_load_state("domcontentloaded")
            elif action_type is BrowserActionType.LOAD_MORE:
                target = page.locator(
                    "button.load-more, [data-action='load-more'], "
                    "button:has-text('Load more')").first
                if await target.count():
                    await target.click()
                    await page.wait_for_timeout(500)
            elif action_type is BrowserActionType.INFINITE_SCROLL:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(500)
            final = page.url
            await self.network_guard._validate_url(final)
            content = await page.content()
            await context.close()
            await browser.close()
        return {
            "url": final, "content": content, "content_type": "text/html",
            "continuation_state": {
                "action_ordinal": 1, "has_more": action_type is not BrowserActionType.NAVIGATE,
            }}


def _http_failure_category(status: str) -> RetrievalFailureCategory:
    return {
        "timeout": RetrievalFailureCategory.TIMEOUT,
        "response_too_large": RetrievalFailureCategory.RESPONSE_TOO_LARGE,
        "redirect_limit_exceeded": RetrievalFailureCategory.REDIRECT_LOOP,
        "unsafe_redirect": RetrievalFailureCategory.UNSAFE_URL,
        "unsafe_url": RetrievalFailureCategory.UNSAFE_URL,
        "private_or_reserved_address": RetrievalFailureCategory.UNSAFE_URL,
        "provider_http_error": RetrievalFailureCategory.HTTP_ERROR,
    }.get(status, RetrievalFailureCategory.RETRYABLE_PROVIDER)


def _safe_failure_message(category: RetrievalFailureCategory) -> str:
    return {
        RetrievalFailureCategory.TIMEOUT: "Retrieval timed out.",
        RetrievalFailureCategory.RATE_LIMIT: "Retrieval was rate limited.",
        RetrievalFailureCategory.RESPONSE_TOO_LARGE: "Response exceeded the configured action limit.",
        RetrievalFailureCategory.UNSAFE_URL: "Retrieval target was unsafe.",
        RetrievalFailureCategory.REDIRECT_LOOP: "Retrieval redirect or continuation loop detected.",
    }.get(category, "Retrieval action failed.")


def _provider_failure(category: RetrievalFailureCategory, retryable: bool = True):
    return RetrievalActionResult(
        outcome="retryable_failure" if retryable else "terminal_failure",
        failure_category=category, public_message=_safe_failure_message(category))


def _firecrawl_http_failure(status: int) -> RetrievalActionResult:
    if status in {401, 403}:
        category, blocker = RetrievalFailureCategory.AUTHENTICATION, True
    elif status == 402:
        category, blocker = RetrievalFailureCategory.CREDIT_EXHAUSTED, True
    elif status == 429:
        category, blocker = RetrievalFailureCategory.RATE_LIMIT, False
    elif status >= 500:
        category, blocker = RetrievalFailureCategory.PROVIDER_OUTAGE, True
    else:
        category, blocker = RetrievalFailureCategory.INVALID_RESPONSE, False
    return RetrievalActionResult(
        outcome="blocked" if blocker else "retryable_failure",
        failure_category=category, public_message=_safe_failure_message(category),
        provider_wide_blocker=blocker)


def _safe_identifier(value: Any) -> str | None:
    return str(value)[:255] if isinstance(value, (str, int)) else None


def _safe_continuation_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {"page", "cursor", "offset", "action_ordinal", "has_more"}
    return {key: item for key, item in value.items()
            if key in allowed and isinstance(item, (str, int, bool, type(None)))}
