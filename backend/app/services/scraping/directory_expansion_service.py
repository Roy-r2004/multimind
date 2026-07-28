"""Phase 5B deterministic directory identification, extraction, and persistence.

Parsing is pure and receives already-decoded content. This module never performs
HTTP, DNS, browser, provider, facility qualification, or publication work.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from enum import Enum
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CrawlEdgeRelationshipType,
    CrawlNodeSourceClassification,
    Phase5WorkStatus,
    ScrapingCrawlEdge,
    ScrapingCrawlNode,
    ScrapingDirectoryObservation,
    ScrapingPhase5WorkJob,
    ScrapingPhase5RetrievalResult,
    ScrapingSourceDocument,
)
from app.services.scraping.discovery_url_service import (
    canonicalize_discovery_target,
    compute_canonical_url_hash,
)
from app.services.scraping.blueprint_execution_plan_service import sha256_hex
from app.services.scraping.phase5_contracts import directory_observation_fingerprint
from app.services.scraping.phase5_job_service import claim_batch

PARSER_VERSION = "phase5b_directory_v1"
MAX_CONTENT_CHARACTERS = 2_000_000
MAX_LISTINGS_PER_SLICE = 2_000
SENSITIVE_QUERY_PARAMETERS = frozenset({
    "token", "access_token", "auth", "authorization", "api_key", "apikey",
    "key", "secret", "signature", "sig", "session", "sessionid", "code",
    "password", "credential", "jwt",
})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DirectoryParserKind(str, Enum):
    AUTO = "auto"
    HTML_CARDS = "html_cards"
    HTML_TABLE = "html_table"
    HTML_LIST = "html_list"
    JSON_COLLECTION = "json_collection"
    JSON_LD_ITEM_LIST = "json_ld_item_list"
    EMBEDDED_JSON = "embedded_json"
    MAP_LISTING_PAYLOAD = "map_listing_payload"


class DirectoryIdentificationOutcome(str, Enum):
    SUPPORTED = "supported_directory"
    NOT_A_DIRECTORY = "not_a_directory"
    EMPTY = "empty_confirmed_directory"
    UNSUPPORTED_REPRESENTATION = "unsupported_content_representation"
    REQUIRES_MANAGED_RENDERING = "requires_managed_rendering"
    REQUIRES_BROWSER_INTERACTION = "requires_browser_interaction"
    MALFORMED = "malformed_content"
    AMBIGUOUS = "ambiguous_structure"
    LIMIT_EXCEEDED = "parser_limit_or_guard_failure"
    REQUIRES_CHUNKED_REPRESENTATION = "requires_chunked_representation"


class PreparedDirectoryContent(StrictModel):
    job_id: str
    organization_id: str
    execution_id: str
    claim_token: str
    parent_crawl_node_id: str
    input_retrieval_result_id: str
    input_source_document_id: str
    input_retrieval_method: str
    listing_page_original_url: str
    listing_page_canonical_url: str
    content_type: str
    decoded_content: str | None = Field(default=None, max_length=MAX_CONTENT_CHARACTERS)
    structured_json: dict[str, Any] | list[Any] | None = None
    embedded_json_records: tuple[dict[str, Any], ...] = ()
    parser_hint: DirectoryParserKind = DirectoryParserKind.AUTO
    source_document_id: str | None = None
    content_fingerprint: str = Field(min_length=64, max_length=64)
    next_entry_ordinal: int = Field(default=0, ge=0)
    entries_completed: int = Field(default=0, ge=0)
    observed_at: datetime

    @field_validator("content_fingerprint")
    @classmethod
    def fingerprint_is_hex(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("content_fingerprint must be lowercase sha256 hex")
        return value

    @model_validator(mode="after")
    def require_one_payload(self) -> "PreparedDirectoryContent":
        supplied = sum((
            self.decoded_content is not None,
            self.structured_json is not None,
            bool(self.embedded_json_records),
        ))
        if supplied != 1:
            raise ValueError("exactly one prepared content payload is required")
        canonical = canonicalize_discovery_target(self.listing_page_canonical_url)
        original = canonicalize_discovery_target(self.listing_page_original_url)
        if not canonical.is_valid or not canonical.is_statically_safe:
            raise ValueError("listing page canonical URL must be statically safe")
        if (not original.is_valid or not original.is_statically_safe or
                original.canonical_url != canonical.canonical_url):
            raise ValueError("listing page original and canonical URLs do not match")
        actual = compute_prepared_content_fingerprint(
            content_type=self.content_type,
            decoded_content=self.decoded_content,
            structured_json=self.structured_json,
            embedded_json_records=self.embedded_json_records)
        if actual != self.content_fingerprint:
            raise ValueError("declared content fingerprint does not match supplied payload")
        return self


class PreparedListing(StrictModel):
    displayed_facility_name: str | None = None
    observed_profile_url: str | None = None
    canonical_profile_url: str | None = None
    observed_official_website_url: str | None = None
    canonical_official_website_url: str | None = None
    displayed_address: str | None = None
    displayed_phone: str | None = None
    displayed_region: str | None = None
    displayed_city: str | None = None
    listing_rank: int = Field(ge=1)
    excerpt_reference: str | None = None
    structured_payload_reference: str | None = None
    extraction_method: DirectoryParserKind


class PreparedContinuation(StrictModel):
    relationship: CrawlEdgeRelationshipType
    observed_url: str | None = None
    canonical_url: str | None = None
    requires_browser_interaction: bool = False
    ordinal_hint: int | None = Field(default=None, ge=0)


class DirectoryIdentificationMetadata(StrictModel):
    parser_kind: DirectoryParserKind | None = None
    supported_pattern: str | None = None
    listing_count: int = Field(ge=0)
    profile_link_count: int = Field(ge=0)
    explicit_website_link_count: int = Field(ge=0)
    ignored_item_count: int = Field(ge=0)
    duplicate_item_count: int = Field(ge=0)
    has_pagination: bool = False
    has_load_more: bool = False
    has_infinite_scroll: bool = False
    parser_version: str = PARSER_VERSION


class PreparedDirectoryExpansion(StrictModel):
    source: PreparedDirectoryContent
    outcome: DirectoryIdentificationOutcome
    error_category: str | None = None
    listings: tuple[PreparedListing, ...] = ()
    continuations: tuple[PreparedContinuation, ...] = ()
    total_entry_count: int = 0
    slice_start_ordinal: int = 0
    slice_end_ordinal: int = 0
    has_more_entries: bool = False
    parser_state_fingerprint: str | None = None
    metadata: DirectoryIdentificationMetadata


class DirectoryExpansionPersistenceResult(StrictModel):
    outcome: str
    observation_count: int = 0
    node_count: int = 0
    edge_count: int = 0


def compute_prepared_content_fingerprint(
    *, content_type: str, decoded_content: str | None,
    structured_json: dict[str, Any] | list[Any] | None,
    embedded_json_records: tuple[dict[str, Any], ...] = (),
) -> str:
    """Versioned representation hash; pass the typed payload directly to sha256_hex."""
    return sha256_hex({
        "schema": "phase5b_prepared_content_v1",
        "content_type": content_type.lower().split(";", 1)[0].strip(),
        "decoded_content": decoded_content,
        "structured_json": structured_json,
        "embedded_json_records": list(embedded_json_records),
    })


async def claim_directory_expansion_batch(
    session: AsyncSession, *, organization_id: str, execution_id: str,
    now: datetime, lease_duration: timedelta, batch_size: int,
):
    """Short-transaction bounded claim returning detached Phase 5A job snapshots."""
    return await claim_batch(
        session, organization_id=organization_id, execution_id=execution_id,
        now=now, lease_duration=lease_duration, batch_size=batch_size,
        selected_tool="directory_expansion")


async def reload_prepared_directory_content(
    session: AsyncSession, *, claimed_job, observed_at: datetime,
) -> PreparedDirectoryContent:
    """Reload the exact immutable directory input from its owned source document."""
    if not claimed_job.input_source_document_id:
        raise ValueError("directory expansion has no durable source document")
    document = await session.scalar(select(ScrapingSourceDocument).where(
        ScrapingSourceDocument.id == claimed_job.input_source_document_id,
        ScrapingSourceDocument.organization_id == claimed_job.organization_id,
        ScrapingSourceDocument.execution_id == claimed_job.execution_id))
    retrieval = await session.scalar(select(ScrapingPhase5RetrievalResult).where(
        ScrapingPhase5RetrievalResult.id == claimed_job.input_retrieval_result_id,
        ScrapingPhase5RetrievalResult.organization_id == claimed_job.organization_id,
        ScrapingPhase5RetrievalResult.execution_id == claimed_job.execution_id))
    if document is None or retrieval is None:
        raise ValueError("directory expansion durable input ownership mismatch")
    retrieval_job = await session.scalar(select(ScrapingPhase5WorkJob).where(
        ScrapingPhase5WorkJob.id == retrieval.work_job_id,
        ScrapingPhase5WorkJob.organization_id == claimed_job.organization_id,
        ScrapingPhase5WorkJob.execution_id == claimed_job.execution_id))
    if (
        retrieval_job is None or
        retrieval_job.crawl_node_id != claimed_job.crawl_node_id or
        retrieval.retrieval_method != claimed_job.input_retrieval_method or
        retrieval.source_document_id != document.id
    ):
        raise ValueError("directory expansion retrieval ownership mismatch")
    document_url = canonicalize_discovery_target(document.final_url)
    retrieval_url = canonicalize_discovery_target(retrieval.final_url or retrieval.requested_url)
    if (
        not document_url.is_valid or not document_url.is_statically_safe or
        not retrieval_url.is_valid or not retrieval_url.is_statically_safe or
        document_url.canonical_url != claimed_job.canonical_url or
        retrieval_url.canonical_url != claimed_job.canonical_url
    ):
        raise ValueError("directory expansion durable URL mismatch")
    content_type = document.content_type
    media_type = content_type.lower().split(";", 1)[0].strip()
    decoded_content, structured_json = document.content_text, None
    if media_type in {"application/json", "application/ld+json"}:
        try:
            structured_json = json.loads(document.content_text)
        except json.JSONDecodeError as exc:
            raise ValueError("durable JSON document is malformed") from exc
        decoded_content = None
    return PreparedDirectoryContent(
        job_id=claimed_job.id, organization_id=claimed_job.organization_id,
        execution_id=claimed_job.execution_id, claim_token=claimed_job.claim_token,
        parent_crawl_node_id=claimed_job.crawl_node_id,
        input_retrieval_result_id=claimed_job.input_retrieval_result_id,
        input_source_document_id=document.id,
        input_retrieval_method=claimed_job.input_retrieval_method,
        listing_page_original_url=document.final_url,
        listing_page_canonical_url=claimed_job.canonical_url,
        content_type=content_type, decoded_content=decoded_content,
        structured_json=structured_json,
        content_fingerprint=claimed_job.input_content_fingerprint,
        next_entry_ordinal=claimed_job.next_entry_ordinal,
        entries_completed=claimed_job.entries_completed, observed_at=observed_at)


def identify_and_prepare_directory(
    source: PreparedDirectoryContent,
) -> PreparedDirectoryExpansion:
    """Parse prepared content outside any database transaction."""
    if _has_conflicting_html_patterns(source):
        return _empty_result(
            source, DirectoryIdentificationOutcome.AMBIGUOUS,
            "ambiguous_directory_structure")
    try:
        parser = _select_parser(source)
        listings, continuations, pattern, ignored = parser(source)
    except _Malformed as exc:
        outcome = (
            DirectoryIdentificationOutcome.UNSUPPORTED_REPRESENTATION
            if exc.category == "unsupported_content_type"
            else DirectoryIdentificationOutcome.MALFORMED)
        return _empty_result(source, outcome, exc.category)
    except _LimitExceeded:
        return _empty_result(
            source, DirectoryIdentificationOutcome.REQUIRES_CHUNKED_REPRESENTATION,
            "requires_chunked_representation")
    except (TypeError, ValueError, json.JSONDecodeError):
        return _empty_result(
            source, DirectoryIdentificationOutcome.MALFORMED, "internal_parser_failure")

    listings = [_normalize_listing(source, item, i + 1) for i, item in enumerate(listings)]
    listings = [item for item in listings if item is not None]
    unique: list[PreparedListing] = []
    seen: set[str] = set()
    duplicates = 0
    for item in listings:
        key = directory_observation_fingerprint(
            organization_id=source.organization_id,
            execution_id=source.execution_id,
            parent_directory_node_id=source.parent_crawl_node_id,
            listing_page_url=source.listing_page_canonical_url,
            profile_url=item.canonical_profile_url,
            official_website_url=item.canonical_official_website_url,
            displayed_facility_name=item.displayed_facility_name,
            displayed_address=item.displayed_address,
            listing_rank=item.listing_rank,
        )
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(item)
    parser_state = sha256_hex({
        "schema": "phase5b_parser_state_v1",
        "content_fingerprint": source.content_fingerprint,
        "parser_version": PARSER_VERSION,
        "entry_identities": [
            {
                "rank": item.listing_rank,
                "profile": item.canonical_profile_url,
                "website": item.canonical_official_website_url,
                "name": item.displayed_facility_name,
                "address": item.displayed_address,
            }
            for item in unique
        ],
    })
    start = source.next_entry_ordinal
    if start > len(unique):
        return _empty_result(
            source, DirectoryIdentificationOutcome.MALFORMED,
            "content_cursor_mismatch")
    end = min(start + MAX_LISTINGS_PER_SLICE, len(unique))
    sliced = unique[start:end]
    if not unique:
        if any(x.requires_browser_interaction for x in continuations):
            outcome = DirectoryIdentificationOutcome.REQUIRES_BROWSER_INTERACTION
            error = "requires_browser_interaction"
        elif _looks_like_javascript_shell(source):
            outcome = DirectoryIdentificationOutcome.REQUIRES_MANAGED_RENDERING
            error = "requires_managed_rendering"
        else:
            outcome = (DirectoryIdentificationOutcome.EMPTY if pattern
                       else DirectoryIdentificationOutcome.NOT_A_DIRECTORY)
            error = None if pattern else "not_a_directory"
    else:
        outcome, error = DirectoryIdentificationOutcome.SUPPORTED, None
    metadata = DirectoryIdentificationMetadata(
        parser_kind=parser.kind, supported_pattern=pattern,
        listing_count=len(sliced),
        profile_link_count=sum(x.canonical_profile_url is not None for x in sliced),
        explicit_website_link_count=sum(
            x.canonical_official_website_url is not None for x in sliced),
        ignored_item_count=ignored, duplicate_item_count=duplicates,
        has_pagination=any(x.relationship is CrawlEdgeRelationshipType.PAGINATION
                           for x in continuations),
        has_load_more=any(x.relationship is CrawlEdgeRelationshipType.LOAD_MORE
                          for x in continuations),
        has_infinite_scroll=any(x.requires_browser_interaction for x in continuations),
    )
    return PreparedDirectoryExpansion(
        source=source, outcome=outcome, error_category=error,
        listings=tuple(sliced), continuations=tuple(continuations), metadata=metadata,
        total_entry_count=len(unique), slice_start_ordinal=start,
        slice_end_ordinal=end, has_more_entries=end < len(unique),
        parser_state_fingerprint=parser_state)


class _Parser:
    def __init__(self, kind: DirectoryParserKind, function):
        self.kind, self.function = kind, function

    def __call__(self, source):
        return self.function(source, self.kind)


PARSER_REGISTRY = {
    DirectoryParserKind.HTML_CARDS: _Parser(DirectoryParserKind.HTML_CARDS, lambda s, k: _parse_html(s, k)),
    DirectoryParserKind.HTML_TABLE: _Parser(DirectoryParserKind.HTML_TABLE, lambda s, k: _parse_html(s, k)),
    DirectoryParserKind.HTML_LIST: _Parser(DirectoryParserKind.HTML_LIST, lambda s, k: _parse_html(s, k)),
    DirectoryParserKind.JSON_COLLECTION: _Parser(DirectoryParserKind.JSON_COLLECTION, lambda s, k: _parse_json(s, k)),
    DirectoryParserKind.JSON_LD_ITEM_LIST: _Parser(DirectoryParserKind.JSON_LD_ITEM_LIST, lambda s, k: _parse_json(s, k)),
    DirectoryParserKind.EMBEDDED_JSON: _Parser(DirectoryParserKind.EMBEDDED_JSON, lambda s, k: _parse_json(s, k)),
    DirectoryParserKind.MAP_LISTING_PAYLOAD: _Parser(DirectoryParserKind.MAP_LISTING_PAYLOAD, lambda s, k: _parse_json(s, k)),
}


def _select_parser(source: PreparedDirectoryContent) -> _Parser:
    if source.parser_hint is not DirectoryParserKind.AUTO:
        return PARSER_REGISTRY[source.parser_hint]
    content_type = source.content_type.lower().split(";", 1)[0].strip()
    if source.structured_json is not None:
        kind = (DirectoryParserKind.JSON_LD_ITEM_LIST
                if _looks_json_ld(source.structured_json)
                else DirectoryParserKind.JSON_COLLECTION)
        return PARSER_REGISTRY[kind]
    if source.embedded_json_records:
        return PARSER_REGISTRY[DirectoryParserKind.EMBEDDED_JSON]
    if content_type in {"text/html", "application/xhtml+xml"}:
        lowered = (source.decoded_content or "").lower()
        if "<table" in lowered:
            return PARSER_REGISTRY[DirectoryParserKind.HTML_TABLE]
        if re.search(
            r"<(?:ul|ol)[^>]+(?:class|id)=[\"'][^\"']*"
            r"(?:facility|provider|directory|listing)", lowered):
            return PARSER_REGISTRY[DirectoryParserKind.HTML_LIST]
        return PARSER_REGISTRY[DirectoryParserKind.HTML_CARDS]
    if content_type in {"application/json", "application/ld+json"}:
        return PARSER_REGISTRY[DirectoryParserKind.JSON_COLLECTION]
    raise _Malformed("unsupported_content_type")


class _Malformed(Exception):
    def __init__(self, category: str):
        self.category = category


class _LimitExceeded(Exception):
    pass


class _Entry:
    def __init__(self, tag: str, attrs: dict[str, str], rank: int):
        self.tag, self.attrs, self.rank = tag, attrs, rank
        self.text: list[str] = []
        self.links: list[tuple[str, str, str]] = []


class _DirectoryHTMLParser(HTMLParser):
    def __init__(self, kind: DirectoryParserKind):
        super().__init__(convert_charrefs=True)
        self.kind = kind
        self.entries: list[_Entry] = []
        self.stack: list[_Entry] = []
        self.anchor: tuple[str, str] | None = None
        self.continuations: list[tuple[str, str | None]] = []
        self.scripts: list[tuple[str, str]] = []
        self._script_type: str | None = None
        self._script_text: list[str] = []
        self.infinite = False
        self.context_stack: list[tuple[str, bool]] = []

    def handle_starttag(self, tag, attrs):
        values = {k: v or "" for k, v in attrs}
        marker = " ".join((values.get("class", ""), values.get("id", ""),
                           values.get("role", ""))).lower()
        recognized_marker = any(
            x in marker for x in ("facility", "provider", "directory", "listing"))
        parent_recognized = self.context_stack[-1][1] if self.context_stack else False
        recognized_context = (
            recognized_marker or parent_recognized or
            (tag == "table" and self.kind is DirectoryParserKind.HTML_TABLE) or
            (tag in {"ul", "ol"} and self.kind is DirectoryParserKind.HTML_LIST)
        )
        is_entry = (
            (tag == "tr" and parent_recognized) or
            (tag == "li" and parent_recognized) or
            (tag in {"article", "section", "div"} and recognized_marker)
        )
        if is_entry:
            entry = _Entry(tag, values, len(self.entries) + 1)
            self.entries.append(entry)
            self.stack.append(entry)
        self.context_stack.append((tag, recognized_context))
        if tag == "a":
            href = values.get("href")
            rel = " ".join((marker, values.get("rel", ""), values.get("aria-label", ""))).lower()
            if href and any(x in rel for x in ("next", "pagination", "load-more", "load more")):
                relation = "load_more" if "load" in rel else "pagination"
                self.continuations.append((relation, href))
            self.anchor = (href or "", rel)
        if tag in {"button", "div"} and "load-more" in marker:
            self.continuations.append(("load_more", values.get("data-url") or None))
        if tag == "button" and "pagination" in marker:
            self.continuations.append(("pagination", values.get("data-url") or None))
        if any(x in marker for x in ("interactive-map", "map-listing")):
            self.continuations.append(
                ("structured_api", values.get("data-api-url") or None))
        if any(x in marker for x in ("infinite-scroll", "infinite_scroll")):
            self.infinite = True
        if tag == "script":
            self._script_type = values.get("type", "").lower()
            self._script_text = []

    def handle_data(self, data):
        text = _clean(data)
        if self.stack and text:
            self.stack[-1].text.append(text)
        if self.anchor and self.stack and text:
            href, rel = self.anchor
            self.stack[-1].links.append((href, rel, text))
        if self._script_type is not None:
            self._script_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self.anchor = None
        if self.stack and tag == self.stack[-1].tag:
            self.stack.pop()
        if tag == "script" and self._script_type is not None:
            self.scripts.append((self._script_type, "".join(self._script_text)))
            self._script_type = None
        for index in range(len(self.context_stack) - 1, -1, -1):
            if self.context_stack[index][0] == tag:
                del self.context_stack[index:]
                break


def _parse_html(source, requested_kind):
    if source.decoded_content is None:
        raise _Malformed("malformed_html")
    parser = _DirectoryHTMLParser(requested_kind)
    try:
        parser.feed(source.decoded_content)
        parser.close()
    except Exception as exc:
        raise _Malformed("malformed_html") from exc
    listings: list[dict[str, Any]] = []
    for entry in parser.entries:
        links = [(href, rel, text) for href, rel, text in entry.links if href]
        if entry.tag == "tr" and not links and not entry.attrs.get("data-name"):
            continue
        website = next((href for href, rel, _ in links
                        if any(x in rel for x in ("official", "website", "homepage"))), None)
        profile = next((href for href, rel, _ in links if href != website), None)
        name = (entry.attrs.get("data-name") or
                next((text for _, rel, text in links if "website" not in rel), None))
        text = _clean(" ".join(entry.text))
        listings.append({
            "name": name or text or None, "profile_url": profile,
            "website": website, "address": entry.attrs.get("data-address"),
            "phone": entry.attrs.get("data-phone"),
            "region": entry.attrs.get("data-region"),
            "city": entry.attrs.get("data-city"), "rank": entry.rank,
            "excerpt_reference": f"html-entry:{entry.rank}",
            "extraction_method": requested_kind,
            "recognized_container": True,
        })
    continuations = _normalize_continuations(source, parser.continuations)
    if parser.infinite and not any(
        item.requires_browser_interaction for item in continuations):
        continuations.append(PreparedContinuation(
            relationship=CrawlEdgeRelationshipType.LOAD_MORE,
            requires_browser_interaction=True))
    for script_type, payload in parser.scripts:
        if script_type in {"application/ld+json", "application/json"} and payload.strip():
            try:
                nested = json.loads(payload)
            except json.JSONDecodeError:
                continue
            nested_source = source.model_copy(update={
                "decoded_content": None, "structured_json": nested,
                "parser_hint": (DirectoryParserKind.JSON_LD_ITEM_LIST
                                if script_type == "application/ld+json"
                                else DirectoryParserKind.EMBEDDED_JSON),
            })
            nested_items, nested_cont, _, ignored = _parse_json(
                nested_source, nested_source.parser_hint)
            listings.extend(nested_items)
            continuations.extend(nested_cont)
            return listings, continuations, f"{requested_kind.value}+embedded_json", ignored
    lowered = source.decoded_content.lower()
    structural = (
        (requested_kind is DirectoryParserKind.HTML_TABLE and "<table" in lowered) or
        (requested_kind is DirectoryParserKind.HTML_LIST and
         ("<ul" in lowered or "<ol" in lowered)) or
        (requested_kind is DirectoryParserKind.HTML_CARDS and any(
            marker in lowered for marker in ("directory-card", "provider-card",
                                               "listing-item", "facility-item")))
    )
    pattern = requested_kind.value if parser.entries or structural else None
    return listings, continuations, pattern, 0


def _parse_json(source, requested_kind):
    if source.structured_json is not None:
        payload: Any = source.structured_json
    elif source.embedded_json_records:
        payload = list(source.embedded_json_records)
    elif source.decoded_content is not None:
        try:
            payload = json.loads(source.decoded_content)
        except json.JSONDecodeError as exc:
            raise _Malformed("malformed_json") from exc
    else:
        raise _Malformed("malformed_json")
    items, path = _find_collection(payload)
    if items is None:
        return [], [], None, 0
    listings, ignored = [], 0
    for rank, raw in enumerate(items, 1):
        if not isinstance(raw, dict):
            ignored += 1
            continue
        item = raw.get("item") if isinstance(raw.get("item"), dict) else raw
        listings.append({
            "name": _first(item, "name", "title", "facilityName", "providerName"),
            "profile_url": _first(item, "profileUrl", "profile_url", "url", "@id"),
            "website": _first(item, "website", "officialWebsite", "official_url"),
            "address": _address(item.get("address")),
            "phone": _first(item, "telephone", "phone"),
            "region": _first(item, "region", "addressRegion"),
            "city": _first(item, "city", "addressLocality"),
            "rank": raw.get("position") or rank,
            "structured_payload_reference": f"{path}[{rank - 1}]",
            "extraction_method": requested_kind,
            "typed_record": True,
        })
    continuations: list[PreparedContinuation] = []
    if isinstance(payload, dict):
        for key, relationship in (
            ("next", CrawlEdgeRelationshipType.PAGINATION),
            ("nextPage", CrawlEdgeRelationshipType.PAGINATION),
            ("loadMoreUrl", CrawlEdgeRelationshipType.LOAD_MORE),
            ("apiUrl", CrawlEdgeRelationshipType.STRUCTURED_API),
        ):
            if isinstance(payload.get(key), str):
                normalized = _safe_url(source.listing_page_canonical_url, payload[key])
                if normalized:
                    continuations.append(PreparedContinuation(
                        relationship=relationship, canonical_url=normalized))
    return listings, continuations, requested_kind.value, ignored


def _find_collection(payload):
    if isinstance(payload, list):
        return payload, "$"
    if not isinstance(payload, dict):
        return None, "$"
    if payload.get("@type") == "ItemList" and isinstance(payload.get("itemListElement"), list):
        return payload["itemListElement"], "$.itemListElement"
    for key in ("results", "items", "listings", "facilities", "providers", "markers"):
        if isinstance(payload.get(key), list):
            return payload[key], f"$.{key}"
    for key in ("data", "props", "pageProps", "response"):
        nested, path = _find_collection(payload.get(key))
        if nested is not None:
            return nested, f"$.{key}{path[1:]}"
    return None, "$"


def _normalize_listing(source, raw, fallback_rank):
    name = _clean(raw.get("name"))
    observed_profile, canonical_profile = _url_pair(
        source.listing_page_canonical_url, raw.get("profile_url"))
    observed_website, canonical_website = _url_pair(
        source.listing_page_canonical_url, raw.get("website"))
    if not any((name, canonical_profile, canonical_website)):
        return None
    has_evidence = bool(
        canonical_profile or canonical_website or raw.get("address") or raw.get("phone")
        or raw.get("typed_record") or raw.get("recognized_container"))
    if not name or not has_evidence:
        return None
    return PreparedListing(
        displayed_facility_name=name,
        observed_profile_url=observed_profile,
        canonical_profile_url=canonical_profile,
        observed_official_website_url=observed_website,
        canonical_official_website_url=canonical_website,
        displayed_address=_clean(raw.get("address")) or None,
        displayed_phone=_clean(raw.get("phone")) or None,
        displayed_region=_clean(raw.get("region")) or None,
        displayed_city=_clean(raw.get("city")) or None,
        listing_rank=int(raw.get("rank") or fallback_rank),
        excerpt_reference=raw.get("excerpt_reference"),
        structured_payload_reference=raw.get("structured_payload_reference"),
        extraction_method=raw.get("extraction_method") or (
            source.parser_hint if source.parser_hint is not DirectoryParserKind.AUTO
            else _infer_method(source)),
    )


def _infer_method(source):
    if source.structured_json is not None:
        return (DirectoryParserKind.JSON_LD_ITEM_LIST if _looks_json_ld(source.structured_json)
                else DirectoryParserKind.JSON_COLLECTION)
    if source.embedded_json_records:
        return DirectoryParserKind.EMBEDDED_JSON
    return DirectoryParserKind.HTML_CARDS


def _safe_url(base, value):
    return _url_pair(base, value)[1]


def _url_pair(base, value):
    if not isinstance(value, str) or not value.strip():
        return None, None
    raw_observed = urljoin(base, value.strip())
    raw_result = canonicalize_discovery_target(raw_observed)
    if not raw_result.is_valid or not raw_result.is_statically_safe:
        return None, None
    parts = urlsplit(raw_observed)
    safe_query = [
        (key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in SENSITIVE_QUERY_PARAMETERS
    ]
    observed = urlunsplit((
        parts.scheme, parts.netloc, parts.path, urlencode(safe_query, doseq=True),
        parts.fragment))
    result = canonicalize_discovery_target(observed)
    if not result.is_valid or not result.is_statically_safe:
        return None, None
    return observed, result.canonical_url


def _normalize_continuations(source, values):
    output, seen = [], set()
    for relation, raw in values:
        observed, url = _url_pair(source.listing_page_canonical_url, raw)
        if not url:
            key = (relation, None)
            if key not in seen:
                seen.add(key)
                output.append(PreparedContinuation(
                    relationship={
                        "load_more": CrawlEdgeRelationshipType.LOAD_MORE,
                        "structured_api": CrawlEdgeRelationshipType.STRUCTURED_API,
                    }.get(relation, CrawlEdgeRelationshipType.PAGINATION),
                    requires_browser_interaction=True))
            continue
        if url == source.listing_page_canonical_url or (relation, url) in seen:
            continue
        seen.add((relation, url))
        output.append(PreparedContinuation(
            relationship={
                "load_more": CrawlEdgeRelationshipType.LOAD_MORE,
                "structured_api": CrawlEdgeRelationshipType.STRUCTURED_API,
            }.get(relation, CrawlEdgeRelationshipType.PAGINATION),
            observed_url=observed, canonical_url=url))
    return output


def _first(value, *keys):
    if not isinstance(value, dict):
        return None
    return next((value[key] for key in keys
                 if isinstance(value.get(key), (str, int, float))), None)


def _address(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _clean(" ".join(str(value.get(key, "")) for key in (
            "streetAddress", "addressLocality", "addressRegion", "postalCode")))
    return None


def _looks_json_ld(value):
    return isinstance(value, dict) and (
        value.get("@type") == "ItemList" or "@context" in value)


def _has_conflicting_html_patterns(source):
    if source.parser_hint is not DirectoryParserKind.AUTO or not source.decoded_content:
        return False
    lowered = source.decoded_content.lower()
    has_table = "<table" in lowered
    has_record_cards = bool(re.search(
        r"<(?:div|article|section)[^>]+(?:class|id)=[\"'][^\"']*"
        r"(?:facility|provider|directory|listing)", lowered))
    has_record_list = bool(re.search(
        r"<(?:ul|ol)[^>]+(?:class|id)=[\"'][^\"']*"
        r"(?:facility|provider|directory|listing)", lowered))
    return sum((has_table, has_record_cards, has_record_list)) > 1


def _looks_like_javascript_shell(source):
    if not source.decoded_content:
        return False
    lowered = source.decoded_content.lower()
    return any(marker in lowered for marker in (
        'id="root"', "id='root'", 'id="app"', "id='app'",
        "__next_data__", "javascript required", "enable javascript"))


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _empty_result(source, outcome, category):
    return PreparedDirectoryExpansion(
        source=source, outcome=outcome, error_category=category,
        metadata=DirectoryIdentificationMetadata(
            listing_count=0, profile_link_count=0,
            explicit_website_link_count=0, ignored_item_count=0,
            duplicate_item_count=0))


async def persist_prepared_expansion(
    session: AsyncSession, prepared: PreparedDirectoryExpansion,
) -> DirectoryExpansionPersistenceResult:
    """Verify the claim first, then atomically persist all graph/observation rows."""
    source = prepared.source
    job = await session.scalar(select(ScrapingPhase5WorkJob).where(
        ScrapingPhase5WorkJob.id == source.job_id,
        ScrapingPhase5WorkJob.organization_id == source.organization_id,
        ScrapingPhase5WorkJob.execution_id == source.execution_id,
        ScrapingPhase5WorkJob.crawl_node_id == source.parent_crawl_node_id,
        ScrapingPhase5WorkJob.work_kind == "directory_expansion",
        ScrapingPhase5WorkJob.status == Phase5WorkStatus.RUNNING,
        ScrapingPhase5WorkJob.claim_token == source.claim_token,
        ScrapingPhase5WorkJob.lease_expires_at > func.now(),
    ).with_for_update())
    if job is None:
        return DirectoryExpansionPersistenceResult(outcome="stale_claim")
    if (
        job.input_retrieval_result_id != source.input_retrieval_result_id or
        job.input_source_document_id != source.input_source_document_id or
        job.input_content_fingerprint != source.content_fingerprint or
        job.input_retrieval_method != source.input_retrieval_method or
        job.next_entry_ordinal != source.next_entry_ordinal or
        job.entries_completed != source.entries_completed or
        (job.parser_state_fingerprint is not None and
         job.parser_state_fingerprint != prepared.parser_state_fingerprint)
    ):
        return DirectoryExpansionPersistenceResult(outcome="input_mismatch")
    retrieval = await session.scalar(select(ScrapingPhase5RetrievalResult).where(
        ScrapingPhase5RetrievalResult.id == source.input_retrieval_result_id,
        ScrapingPhase5RetrievalResult.organization_id == source.organization_id,
        ScrapingPhase5RetrievalResult.execution_id == source.execution_id))
    if retrieval is None or retrieval.retrieval_method != source.input_retrieval_method:
        return DirectoryExpansionPersistenceResult(outcome="ownership_mismatch")
    retrieval_job = await session.get(ScrapingPhase5WorkJob, retrieval.work_job_id)
    if retrieval_job is None or retrieval_job.crawl_node_id != source.parent_crawl_node_id:
        return DirectoryExpansionPersistenceResult(outcome="ownership_mismatch")
    document = await session.scalar(select(ScrapingSourceDocument).where(
        ScrapingSourceDocument.id == source.input_source_document_id,
        ScrapingSourceDocument.organization_id == source.organization_id,
        ScrapingSourceDocument.execution_id == source.execution_id))
    if document is None or retrieval.source_document_id != document.id:
        return DirectoryExpansionPersistenceResult(outcome="ownership_mismatch")
    try:
        durable = await reload_prepared_directory_content(
            session, claimed_job=type("_Claim", (), {
                "id": job.id, "organization_id": job.organization_id,
                "execution_id": job.execution_id, "claim_token": job.claim_token,
                "crawl_node_id": job.crawl_node_id, "canonical_url": job.canonical_url,
                "input_retrieval_result_id": job.input_retrieval_result_id,
                "input_source_document_id": job.input_source_document_id,
                "input_content_fingerprint": job.input_content_fingerprint,
                "input_retrieval_method": job.input_retrieval_method,
                "next_entry_ordinal": job.next_entry_ordinal,
                "entries_completed": job.entries_completed,
            })(), observed_at=source.observed_at)
    except ValueError:
        return DirectoryExpansionPersistenceResult(outcome="input_mismatch")
    if durable.content_fingerprint != source.content_fingerprint:
        return DirectoryExpansionPersistenceResult(outcome="input_mismatch")
    job.continuation_markers_json = [
        {
            "relationship": marker.relationship.value,
            "observed_url": marker.observed_url,
            "canonical_url": marker.canonical_url,
            "requires_browser_interaction": marker.requires_browser_interaction,
            "ordinal_hint": marker.ordinal_hint,
        }
        for marker in prepared.continuations
    ]
    if prepared.outcome is not DirectoryIdentificationOutcome.SUPPORTED:
        job.status = {
            DirectoryIdentificationOutcome.EMPTY: Phase5WorkStatus.SUCCEEDED,
            DirectoryIdentificationOutcome.NOT_A_DIRECTORY: Phase5WorkStatus.REJECTED,
            DirectoryIdentificationOutcome.AMBIGUOUS: Phase5WorkStatus.REJECTED,
            DirectoryIdentificationOutcome.UNSUPPORTED_REPRESENTATION: Phase5WorkStatus.BLOCKED,
            DirectoryIdentificationOutcome.REQUIRES_MANAGED_RENDERING: Phase5WorkStatus.BLOCKED,
            DirectoryIdentificationOutcome.REQUIRES_BROWSER_INTERACTION: Phase5WorkStatus.BLOCKED,
            DirectoryIdentificationOutcome.REQUIRES_CHUNKED_REPRESENTATION:
                Phase5WorkStatus.BLOCKED,
        }.get(prepared.outcome, Phase5WorkStatus.FAILED)
        job.completed_at = source.observed_at
        job.last_error_category = prepared.error_category
        job.last_error_message = _public_failure_message(prepared.outcome)
        job.expansion_outcome = prepared.outcome.value
        job.requires_managed_rendering = prepared.outcome in {
            DirectoryIdentificationOutcome.UNSUPPORTED_REPRESENTATION,
            DirectoryIdentificationOutcome.REQUIRES_MANAGED_RENDERING,
        }
        job.requires_browser_interaction = (
            prepared.outcome is DirectoryIdentificationOutcome.REQUIRES_BROWSER_INTERACTION)
        job.expansion_completed = (
            prepared.outcome in {
                DirectoryIdentificationOutcome.EMPTY,
                DirectoryIdentificationOutcome.NOT_A_DIRECTORY,
            })
        job.claim_token = job.claimed_at = job.lease_expires_at = None
        await session.flush()
        return DirectoryExpansionPersistenceResult(outcome=prepared.outcome.value)

    nodes, edges, observations = 0, 0, 0
    for listing in prepared.listings:
        profile = await _upsert_node(
            session, source, listing.canonical_profile_url,
            CrawlNodeSourceClassification.FACILITY_PROFILE)
        website = await _upsert_node(
            session, source, listing.canonical_official_website_url,
            CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE)
        nodes += int(profile is not None) + int(website is not None)
        if profile:
            edges += await _upsert_edge(
                session, source, source.parent_crawl_node_id, profile.id,
                CrawlEdgeRelationshipType.DIRECTORY_TO_PROFILE, job)
        if website:
            edges += await _upsert_edge(
                session, source, profile.id if profile else source.parent_crawl_node_id,
                website.id,
                (CrawlEdgeRelationshipType.PROFILE_TO_OFFICIAL_SITE if profile
                 else CrawlEdgeRelationshipType.DIRECTORY_TO_OFFICIAL_SITE), job)
        fingerprint = directory_observation_fingerprint(
            organization_id=source.organization_id,
            execution_id=source.execution_id,
            parent_directory_node_id=source.parent_crawl_node_id,
            listing_page_url=source.listing_page_canonical_url,
            profile_url=listing.canonical_profile_url,
            official_website_url=listing.canonical_official_website_url,
            displayed_facility_name=listing.displayed_facility_name,
            displayed_address=listing.displayed_address,
            listing_rank=listing.listing_rank)
        observations += await _upsert_observation(
            session, source, listing, fingerprint,
            profile.id if profile else None, website.id if website else None)
    for continuation in prepared.continuations:
        if continuation.canonical_url:
            node = await _upsert_node(
                session, source, continuation.canonical_url,
                CrawlNodeSourceClassification.DIRECTORY)
            if node:
                edges += await _upsert_edge(
                    session, source, source.parent_crawl_node_id, node.id,
                    continuation.relationship, job)
    job.next_entry_ordinal = prepared.slice_end_ordinal
    job.entries_completed = prepared.slice_end_ordinal
    job.last_processed_slice_count = len(prepared.listings)
    job.expansion_parser_version = PARSER_VERSION
    job.parser_state_fingerprint = prepared.parser_state_fingerprint
    job.expansion_outcome = prepared.outcome.value
    job.requires_browser_interaction = any(
        marker.requires_browser_interaction for marker in prepared.continuations)
    job.expansion_completed = not prepared.has_more_entries
    job.status = (Phase5WorkStatus.PENDING if prepared.has_more_entries
                  else Phase5WorkStatus.SUCCEEDED)
    job.completed_at = None if prepared.has_more_entries else source.observed_at
    job.last_error_category = job.last_error_message = None
    job.claim_token = job.claimed_at = job.lease_expires_at = None
    await session.flush()
    return DirectoryExpansionPersistenceResult(
        outcome="persisted", observation_count=observations,
        node_count=nodes, edge_count=edges)


async def _upsert_node(session, source, url, classification):
    if not url:
        return None
    url_hash = compute_canonical_url_hash(url)
    existing = await session.scalar(select(ScrapingCrawlNode).where(
        ScrapingCrawlNode.organization_id == source.organization_id,
        ScrapingCrawlNode.execution_id == source.execution_id,
        ScrapingCrawlNode.canonical_url_hash == url_hash))
    if existing:
        return existing
    parsed = canonicalize_discovery_target(url)
    row = ScrapingCrawlNode(
        organization_id=source.organization_id, execution_id=source.execution_id,
        canonical_url=url, canonical_url_hash=url_hash,
        hostname=parsed.hostname, domain=parsed.normalized_domain,
        source_classification=classification, first_seen_at=source.observed_at)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(select(ScrapingCrawlNode).where(
            ScrapingCrawlNode.organization_id == source.organization_id,
            ScrapingCrawlNode.execution_id == source.execution_id,
            ScrapingCrawlNode.canonical_url_hash == url_hash))
        if existing is None:
            raise
        return existing
    return row


async def _upsert_edge(session, source, from_id, to_id, relationship, job):
    if from_id == to_id:
        return 0
    existing = await session.scalar(select(ScrapingCrawlEdge).where(
        ScrapingCrawlEdge.organization_id == source.organization_id,
        ScrapingCrawlEdge.execution_id == source.execution_id,
        ScrapingCrawlEdge.from_node_id == from_id,
        ScrapingCrawlEdge.to_node_id == to_id,
        ScrapingCrawlEdge.relationship_type == relationship))
    if existing:
        return 0
    row = ScrapingCrawlEdge(
        organization_id=source.organization_id, execution_id=source.execution_id,
        from_node_id=from_id, to_node_id=to_id, relationship_type=relationship,
        discovery_query_id=job.discovery_query_id,
        source_candidate_id=job.source_candidate_id)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(select(ScrapingCrawlEdge).where(
            ScrapingCrawlEdge.organization_id == source.organization_id,
            ScrapingCrawlEdge.execution_id == source.execution_id,
            ScrapingCrawlEdge.from_node_id == from_id,
            ScrapingCrawlEdge.to_node_id == to_id,
            ScrapingCrawlEdge.relationship_type == relationship))
        if existing is None:
            raise
        return 0
    return 1


async def _upsert_observation(session, source, listing, fingerprint, profile_id, website_id):
    existing = await session.scalar(select(ScrapingDirectoryObservation).where(
        ScrapingDirectoryObservation.organization_id == source.organization_id,
        ScrapingDirectoryObservation.execution_id == source.execution_id,
        ScrapingDirectoryObservation.observation_fingerprint == fingerprint))
    if existing:
        return 0
    row = ScrapingDirectoryObservation(
        organization_id=source.organization_id, execution_id=source.execution_id,
        work_job_id=source.job_id, observation_fingerprint=fingerprint,
        displayed_facility_name=listing.displayed_facility_name,
        listing_page_url=source.listing_page_original_url,
        profile_url=listing.observed_profile_url,
        official_website_url=listing.observed_official_website_url,
        displayed_address=listing.displayed_address,
        displayed_phone=listing.displayed_phone,
        displayed_region=listing.displayed_region,
        displayed_city=listing.displayed_city,
        directory_source=canonicalize_discovery_target(
            source.listing_page_canonical_url).normalized_domain or "directory",
        listing_rank=listing.listing_rank,
        raw_excerpt=listing.excerpt_reference,
        structured_payload_reference=listing.structured_payload_reference,
        parent_directory_node_id=source.parent_crawl_node_id,
        emitted_profile_node_id=profile_id, emitted_website_node_id=website_id,
        extraction_method=listing.extraction_method.value,
        observed_at=source.observed_at)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(select(ScrapingDirectoryObservation).where(
            ScrapingDirectoryObservation.organization_id == source.organization_id,
            ScrapingDirectoryObservation.execution_id == source.execution_id,
            ScrapingDirectoryObservation.observation_fingerprint == fingerprint))
        if existing is None:
            raise
        return 0
    return 1


def _public_failure_message(outcome):
    return {
        DirectoryIdentificationOutcome.NOT_A_DIRECTORY: "Content is not a directory.",
        DirectoryIdentificationOutcome.UNSUPPORTED_REPRESENTATION:
            "Content representation requires another retrieval method.",
        DirectoryIdentificationOutcome.REQUIRES_MANAGED_RENDERING:
            "Content requires managed rendering.",
        DirectoryIdentificationOutcome.REQUIRES_BROWSER_INTERACTION:
            "Content requires browser interaction.",
        DirectoryIdentificationOutcome.REQUIRES_CHUNKED_REPRESENTATION:
            "Content requires a durable chunked representation.",
        DirectoryIdentificationOutcome.MALFORMED: "Malformed prepared directory content.",
        DirectoryIdentificationOutcome.AMBIGUOUS: "Ambiguous directory structure.",
        DirectoryIdentificationOutcome.LIMIT_EXCEEDED: "Directory parser technical limit exceeded.",
    }.get(outcome)
