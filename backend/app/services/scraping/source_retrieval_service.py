"""Secure retrieval of persisted source candidates.

The service validates DNS immediately before each request and every redirect. httpx still performs
its own connection-time resolution, so this is DNS rebinding resistant but not perfect DNS pinning.
True pinning would require a lower-level transport that connects to a vetted address while preserving
Host/SNI; Step 2A keeps the strongest practical guard available with the current async HTTP stack.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from email.parser import Parser
from types import TracebackType
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import (
    ScrapingExecution,
    ScrapingSourceCandidate,
    ScrapingSourceDocument,
    ScrapingSourceRetrievalAttempt,
    SourceRetrievalAttemptStatus,
    SourceRetrievalRobotsStatus,
)

MAX_ERROR_MESSAGE_LENGTH = 500
MAX_IDEMPOTENCY_KEY_LENGTH = 160
SAFE_ACCEPT = "text/html,text/plain,application/json,application/xml,text/xml;q=0.9,*/*;q=0.1"
LOCALHOST_HOSTS = {"localhost", "localhost.localdomain"}
METADATA_HOSTS = {
    "metadata",
    "metadata.google.internal",
    "169.254.169.254.nip.io",
}
METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}
SUPPORTED_CONTENT_TYPES = {
    "text/html",
    "text/plain",
    "application/json",
    "application/xml",
    "text/xml",
    "application/xhtml+xml",
    "application/ld+json",
}


class SourceRetrievalContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    execution_id: str
    source_candidate_id: str
    coverage_cell_id: str | None = None
    task_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)


class SourceRetrievalSummary(BaseModel):
    attempt_id: str
    status: str
    requested_url: str
    final_url: str | None = None
    redirect_count: int
    http_status: int | None = None
    content_type: str | None = None
    bytes_received: int | None = None
    robots_status: str | None = None
    failure_classification: str | None = None
    safe_error_message: str | None = None
    document_id: str | None = None
    content_sha256: str | None = None


class SourceRetrievalError(Exception):
    def __init__(
        self,
        status: SourceRetrievalAttemptStatus,
        message: str,
        *,
        robots_status: SourceRetrievalRobotsStatus | None = None,
        final_url: str | None = None,
        redirect_count: int = 0,
        http_status: int | None = None,
        content_type: str | None = None,
        declared_content_length: int | None = None,
        bytes_received: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.robots_status = robots_status
        self.final_url = final_url
        self.redirect_count = redirect_count
        self.http_status = http_status
        self.content_type = content_type
        self.declared_content_length = declared_content_length
        self.bytes_received = bytes_received
        self.metadata = metadata or {}


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    scheme: str
    hostname: str
    port: int
    robots_url: str


@dataclass
class FetchResult:
    final_url: str
    redirect_count: int
    http_status: int
    headers: httpx.Headers
    content_type: str | None
    declared_content_length: int | None
    bytes_received: int
    content: bytes


Resolver = Callable[[str, int], Awaitable[list[str]]]


class SourceRetrievalService:
    def __init__(
        self,
        *,
        client_factory: Callable[..., Any] | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._client_factory = client_factory or httpx.AsyncClient
        self._resolver = resolver or self._resolve_hostname

    async def retrieve(
        self,
        db: AsyncSession,
        context: SourceRetrievalContext,
    ) -> SourceRetrievalSummary:
        candidate = await self._load_candidate(db, context)
        existing = await self._existing_attempt(db, context)
        if existing is not None and existing.completed_at is not None:
            document = await self._document_for_attempt_or_hash(db, existing)
            return self._summary(existing, document)

        now = datetime.now(UTC)
        attempt = existing or ScrapingSourceRetrievalAttempt(
            organization_id=context.organization_id,
            execution_id=context.execution_id,
            source_candidate_id=context.source_candidate_id,
            coverage_cell_id=context.coverage_cell_id,
            task_id=context.task_id,
            status=SourceRetrievalAttemptStatus.FAILED,
            requested_url=candidate.canonical_url,
            redirect_count=0,
            started_at=now,
            idempotency_key=context.idempotency_key[:MAX_IDEMPOTENCY_KEY_LENGTH],
            metadata_json={},
        )
        if existing is None:
            db.add(attempt)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                existing = await self._existing_attempt(db, context)
                if existing is not None and existing.completed_at is not None:
                    document = await self._document_for_attempt_or_hash(db, existing)
                    return self._summary(existing, document)
                raise

        try:
            validated = await self._validate_url(candidate.canonical_url)
            robots_status = await self._check_robots(validated)
            if robots_status not in {SourceRetrievalRobotsStatus.ALLOWED, SourceRetrievalRobotsStatus.NO_RULES}:
                raise SourceRetrievalError(
                    SourceRetrievalAttemptStatus.BLOCKED_BY_ROBOTS,
                    "Robots policy blocked retrieval",
                    robots_status=robots_status,
                    final_url=validated.url,
                )
            fetched = await self._fetch(validated)
            content_type = _media_type(fetched.content_type)
            if not _is_supported_content_type(content_type):
                raise SourceRetrievalError(
                    SourceRetrievalAttemptStatus.UNSUPPORTED_CONTENT_TYPE,
                    "Unsupported content type for Step 2A",
                    robots_status=robots_status,
                    final_url=fetched.final_url,
                    redirect_count=fetched.redirect_count,
                    http_status=fetched.http_status,
                    content_type=fetched.content_type,
                    declared_content_length=fetched.declared_content_length,
                    bytes_received=fetched.bytes_received,
                )
            charset = _detect_charset(fetched.content_type)
            try:
                content_text = fetched.content.decode(charset or "utf-8", errors="replace")
            except LookupError as exc:
                raise SourceRetrievalError(
                    SourceRetrievalAttemptStatus.MALFORMED_CONTENT,
                    "Unsupported declared charset",
                    robots_status=robots_status,
                    final_url=fetched.final_url,
                    redirect_count=fetched.redirect_count,
                    http_status=fetched.http_status,
                    content_type=fetched.content_type,
                    declared_content_length=fetched.declared_content_length,
                    bytes_received=fetched.bytes_received,
                ) from exc

            content_hash = hashlib.sha256(fetched.content).hexdigest()
            attempt.status = SourceRetrievalAttemptStatus.SUCCEEDED
            attempt.final_url = fetched.final_url
            attempt.redirect_count = fetched.redirect_count
            attempt.http_status = fetched.http_status
            attempt.content_type = fetched.content_type
            attempt.declared_content_length = fetched.declared_content_length
            attempt.bytes_received = fetched.bytes_received
            attempt.robots_status = robots_status
            attempt.failure_classification = None
            attempt.safe_error_message = None
            attempt.completed_at = datetime.now(UTC)
            attempt.metadata_json = {"dns_rebinding_note": "dns_validated_before_connect_not_pinned"}
            document = await self._persist_document(
                db,
                context,
                attempt,
                final_url=fetched.final_url,
                content_type=fetched.content_type or content_type or "application/octet-stream",
                charset=charset,
                content_sha256=content_hash,
                content_text=content_text,
                extracted_text=content_text if content_type in {"text/plain", "application/json", "application/xml", "text/xml"} else None,
                byte_size=fetched.bytes_received,
            )
            await db.commit()
            await db.refresh(attempt)
            await db.refresh(document)
            return self._summary(attempt, document)
        except SourceRetrievalError as exc:
            attempt.status = exc.status
            attempt.final_url = exc.final_url
            attempt.redirect_count = exc.redirect_count
            attempt.http_status = exc.http_status
            attempt.content_type = exc.content_type
            attempt.declared_content_length = exc.declared_content_length
            attempt.bytes_received = exc.bytes_received
            attempt.robots_status = exc.robots_status
            attempt.failure_classification = exc.status.value
            attempt.safe_error_message = _safe_error(exc.message)
            attempt.completed_at = datetime.now(UTC)
            attempt.metadata_json = _safe_metadata(exc.metadata)
            await db.commit()
            await db.refresh(attempt)
            return self._summary(attempt, None)
        except asyncio.CancelledError:
            attempt.status = SourceRetrievalAttemptStatus.CANCELLED
            attempt.failure_classification = SourceRetrievalAttemptStatus.CANCELLED.value
            attempt.safe_error_message = "Retrieval cancelled"
            attempt.completed_at = datetime.now(UTC)
            await db.commit()
            raise
        except Exception:
            attempt.status = SourceRetrievalAttemptStatus.FAILED
            attempt.failure_classification = SourceRetrievalAttemptStatus.FAILED.value
            attempt.safe_error_message = "Retrieval failed"
            attempt.completed_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(attempt)
            return self._summary(attempt, None)

    async def list_attempts(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        source_candidate_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScrapingSourceRetrievalAttempt]:
        await self._assert_execution_access(db, auth, execution_id)
        query = (
            select(ScrapingSourceRetrievalAttempt)
            .where(
                ScrapingSourceRetrievalAttempt.organization_id == auth.org_id,
                ScrapingSourceRetrievalAttempt.execution_id == execution_id,
            )
            .order_by(ScrapingSourceRetrievalAttempt.started_at.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 10000))
        )
        if source_candidate_id:
            query = query.where(ScrapingSourceRetrievalAttempt.source_candidate_id == source_candidate_id)
        if status:
            query = query.where(ScrapingSourceRetrievalAttempt.status == SourceRetrievalAttemptStatus(status))
        result = await db.execute(query)
        return list(result.scalars().all())

    async def list_documents(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        source_candidate_id: str | None = None,
        content_sha256: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScrapingSourceDocument]:
        await self._assert_execution_access(db, auth, execution_id)
        query = (
            select(ScrapingSourceDocument)
            .where(
                ScrapingSourceDocument.organization_id == auth.org_id,
                ScrapingSourceDocument.execution_id == execution_id,
            )
            .order_by(ScrapingSourceDocument.retrieval_timestamp.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 10000))
        )
        if source_candidate_id:
            query = query.where(ScrapingSourceDocument.source_candidate_id == source_candidate_id)
        if content_sha256:
            query = query.where(ScrapingSourceDocument.content_sha256 == content_sha256)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def _load_candidate(
        self, db: AsyncSession, context: SourceRetrievalContext
    ) -> ScrapingSourceCandidate:
        result = await db.execute(
            select(ScrapingSourceCandidate).where(
                ScrapingSourceCandidate.id == context.source_candidate_id,
                ScrapingSourceCandidate.organization_id == context.organization_id,
                ScrapingSourceCandidate.execution_id == context.execution_id,
            )
        )
        candidate = result.scalar_one_or_none()
        if candidate is None:
            raise NotFoundError("ScrapingSourceCandidate", context.source_candidate_id)
        if context.coverage_cell_id and candidate.coverage_cell_id != context.coverage_cell_id:
            raise ValidationError("Source candidate coverage cell does not match retrieval context.")
        return candidate

    async def _existing_attempt(
        self, db: AsyncSession, context: SourceRetrievalContext
    ) -> ScrapingSourceRetrievalAttempt | None:
        result = await db.execute(
            select(ScrapingSourceRetrievalAttempt).where(
                ScrapingSourceRetrievalAttempt.organization_id == context.organization_id,
                ScrapingSourceRetrievalAttempt.idempotency_key == context.idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def _document_for_attempt_or_hash(
        self, db: AsyncSession, attempt: ScrapingSourceRetrievalAttempt
    ) -> ScrapingSourceDocument | None:
        result = await db.execute(
            select(ScrapingSourceDocument).where(
                ScrapingSourceDocument.organization_id == attempt.organization_id,
                ScrapingSourceDocument.retrieval_attempt_id == attempt.id,
            )
        )
        document = result.scalar_one_or_none()
        if document is not None:
            return document
        content_hash = (attempt.metadata_json or {}).get("content_sha256")
        if not content_hash:
            return None
        result = await db.execute(
            select(ScrapingSourceDocument).where(
                ScrapingSourceDocument.organization_id == attempt.organization_id,
                ScrapingSourceDocument.source_candidate_id == attempt.source_candidate_id,
                ScrapingSourceDocument.content_sha256 == content_hash,
            )
        )
        return result.scalar_one_or_none()

    async def _persist_document(
        self,
        db: AsyncSession,
        context: SourceRetrievalContext,
        attempt: ScrapingSourceRetrievalAttempt,
        *,
        final_url: str,
        content_type: str,
        charset: str | None,
        content_sha256: str,
        content_text: str,
        extracted_text: str | None,
        byte_size: int,
    ) -> ScrapingSourceDocument:
        existing_result = await db.execute(
            select(ScrapingSourceDocument).where(
                ScrapingSourceDocument.organization_id == context.organization_id,
                ScrapingSourceDocument.source_candidate_id == context.source_candidate_id,
                ScrapingSourceDocument.content_sha256 == content_sha256,
            )
        )
        existing = existing_result.scalar_one_or_none()
        attempt.metadata_json = {
            **(attempt.metadata_json or {}),
            "content_sha256": content_sha256,
            "document_deduplicated": existing is not None,
        }
        if existing is not None:
            return existing
        document = ScrapingSourceDocument(
            organization_id=context.organization_id,
            execution_id=context.execution_id,
            source_candidate_id=context.source_candidate_id,
            retrieval_attempt_id=attempt.id,
            final_url=final_url,
            content_type=content_type[:255],
            charset=(charset or "")[:80] or None,
            content_sha256=content_sha256,
            content_text=content_text,
            extracted_text=extracted_text,
            byte_size=byte_size,
            retrieval_timestamp=datetime.now(UTC),
            metadata_json={"storage": "bounded_text", "max_bytes": get_settings().source_retrieval_max_bytes},
        )
        db.add(document)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing_result = await db.execute(
                select(ScrapingSourceDocument).where(
                    ScrapingSourceDocument.organization_id == context.organization_id,
                    ScrapingSourceDocument.source_candidate_id == context.source_candidate_id,
                    ScrapingSourceDocument.content_sha256 == content_sha256,
                )
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                return existing
            raise
        return document

    async def _validate_url(self, raw_url: str) -> ValidatedUrl:
        try:
            parsed = urlsplit((raw_url or "").strip())
        except ValueError as exc:
            raise SourceRetrievalError(SourceRetrievalAttemptStatus.UNSAFE_URL, "Malformed URL") from exc
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise SourceRetrievalError(SourceRetrievalAttemptStatus.UNSAFE_URL, "Only HTTP and HTTPS URLs are supported")
        if parsed.username or parsed.password:
            raise SourceRetrievalError(SourceRetrievalAttemptStatus.UNSAFE_URL, "Credential-bearing URLs are not allowed")
        if not parsed.hostname:
            raise SourceRetrievalError(SourceRetrievalAttemptStatus.UNSAFE_URL, "URL hostname is required")
        hostname = parsed.hostname.rstrip(".").lower()
        _validate_hostname(hostname)
        port = parsed.port or (443 if scheme == "https" else 80)
        if port not in set(get_settings().source_retrieval_allowed_ports):
            raise SourceRetrievalError(SourceRetrievalAttemptStatus.UNSAFE_URL, "URL port is not allowed")
        path = parsed.path or "/"
        netloc = hostname if parsed.port is None else f"{hostname}:{port}"
        normalized = urlunsplit((scheme, netloc, path, parsed.query, ""))
        reparsed = urlsplit(normalized)
        if reparsed.scheme.lower() not in {"http", "https"} or reparsed.hostname != hostname:
            raise SourceRetrievalError(SourceRetrievalAttemptStatus.UNSAFE_URL, "URL became unsafe after normalization")
        await self._validate_dns(hostname, port)
        robots_url = urlunsplit((scheme, netloc, "/robots.txt", "", ""))
        return ValidatedUrl(url=normalized, scheme=scheme, hostname=hostname, port=port, robots_url=robots_url)

    async def _validate_dns(self, hostname: str, port: int) -> None:
        try:
            addresses = await self._resolver(hostname, port)
        except OSError as exc:
            raise SourceRetrievalError(
                SourceRetrievalAttemptStatus.DNS_RESOLUTION_FAILED,
                "DNS resolution failed",
            ) from exc
        if not addresses:
            raise SourceRetrievalError(SourceRetrievalAttemptStatus.DNS_RESOLUTION_FAILED, "DNS resolution returned no addresses")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise SourceRetrievalError(
                    SourceRetrievalAttemptStatus.DNS_RESOLUTION_FAILED,
                    "DNS resolution returned an invalid address",
                ) from exc
            if _unsafe_ip(ip):
                raise SourceRetrievalError(
                    SourceRetrievalAttemptStatus.PRIVATE_OR_RESERVED_ADDRESS,
                    "Resolved address is not allowed",
                )

    async def _resolve_hostname(self, hostname: str, port: int) -> list[str]:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        return sorted({info[4][0] for info in infos})

    async def _check_robots(self, validated: ValidatedUrl) -> SourceRetrievalRobotsStatus:
        if get_settings().source_retrieval_robots_policy != "respect":
            return SourceRetrievalRobotsStatus.ALLOWED
        try:
            robots_result = await self._fetch(
                await self._validate_url(validated.robots_url),
                for_robots=True,
            )
        except SourceRetrievalError as exc:
            if exc.status in {
                SourceRetrievalAttemptStatus.TIMEOUT,
                SourceRetrievalAttemptStatus.UNSAFE_REDIRECT,
                SourceRetrievalAttemptStatus.REDIRECT_LIMIT_EXCEEDED,
                SourceRetrievalAttemptStatus.PROVIDER_HTTP_ERROR,
                SourceRetrievalAttemptStatus.RESPONSE_TOO_LARGE,
            }:
                return SourceRetrievalRobotsStatus.UNAVAILABLE
            return SourceRetrievalRobotsStatus.BLOCKED
        if robots_result.http_status in {404, 410}:
            return SourceRetrievalRobotsStatus.NO_RULES
        if robots_result.http_status in {401, 403}:
            return SourceRetrievalRobotsStatus.BLOCKED
        if robots_result.http_status >= 500:
            return SourceRetrievalRobotsStatus.UNAVAILABLE
        if robots_result.http_status != 200:
            return SourceRetrievalRobotsStatus.UNAVAILABLE
        parser = RobotFileParser()
        parser.set_url(validated.robots_url)
        try:
            parser.parse(robots_result.content.decode("utf-8", errors="replace").splitlines())
        except Exception:
            return SourceRetrievalRobotsStatus.UNAVAILABLE
        allowed = parser.can_fetch(get_settings().source_retrieval_user_agent, validated.url)
        return SourceRetrievalRobotsStatus.ALLOWED if allowed else SourceRetrievalRobotsStatus.DISALLOWED

    async def _fetch(self, start_url: ValidatedUrl, *, for_robots: bool = False) -> FetchResult:
        redirects = 0
        current = start_url
        max_redirects = get_settings().source_retrieval_max_redirects
        while True:
            response = await self._single_get(current, for_robots=for_robots)
            if response.http_status in {301, 302, 303, 307, 308}:
                if redirects >= max_redirects:
                    raise SourceRetrievalError(
                        SourceRetrievalAttemptStatus.REDIRECT_LIMIT_EXCEEDED,
                        "Redirect limit exceeded",
                        final_url=current.url,
                        redirect_count=redirects,
                        http_status=response.http_status,
                    )
                location = response.headers.get("location")
                if not location:
                    raise SourceRetrievalError(
                        SourceRetrievalAttemptStatus.UNSAFE_REDIRECT,
                        "Redirect response did not include a Location header",
                        final_url=current.url,
                        redirect_count=redirects,
                        http_status=response.http_status,
                    )
                try:
                    current = await self._validate_url(urljoin(current.url, location))
                except SourceRetrievalError as exc:
                    raise SourceRetrievalError(
                        SourceRetrievalAttemptStatus.UNSAFE_REDIRECT,
                        "Redirect target is unsafe",
                        final_url=current.url,
                        redirect_count=redirects + 1,
                        http_status=response.http_status,
                    ) from exc
                redirects += 1
                continue
            return FetchResult(
                final_url=current.url,
                redirect_count=redirects,
                http_status=response.http_status,
                headers=response.headers,
                content_type=response.content_type,
                declared_content_length=response.declared_content_length,
                bytes_received=response.bytes_received,
                content=response.content,
            )

    async def _single_get(self, url: ValidatedUrl, *, for_robots: bool) -> FetchResult:
        timeout = httpx.Timeout(
            timeout=get_settings().source_retrieval_timeout_seconds,
            connect=get_settings().source_retrieval_connect_timeout_seconds,
            read=get_settings().source_retrieval_timeout_seconds,
            write=get_settings().source_retrieval_timeout_seconds,
            pool=get_settings().source_retrieval_connect_timeout_seconds,
        )
        headers = {
            "Accept": "text/plain,*/*;q=0.1" if for_robots else SAFE_ACCEPT,
            "User-Agent": get_settings().source_retrieval_user_agent,
        }
        try:
            async with _client_context(
                self._client_factory(
                    timeout=timeout,
                    follow_redirects=False,
                    verify=True,
                    headers=headers,
                    cookies=None,
                )
            ) as client:
                request = client.build_request("GET", url.url, headers=headers)
                response = await client.send(request, stream=True)
                try:
                    content = await self._read_bounded(response)
                finally:
                    await response.aclose()
        except httpx.TimeoutException as exc:
            raise SourceRetrievalError(SourceRetrievalAttemptStatus.TIMEOUT, "Retrieval timed out", final_url=url.url) from exc
        except httpx.HTTPError as exc:
            raise SourceRetrievalError(
                SourceRetrievalAttemptStatus.CONNECTION_FAILED,
                "HTTP connection failed",
                final_url=url.url,
            ) from exc
        declared = _content_length(response.headers.get("content-length"))
        content_type = _bounded_optional(response.headers.get("content-type"), 255)
        if response.status_code >= 400 and not for_robots:
            raise SourceRetrievalError(
                SourceRetrievalAttemptStatus.PROVIDER_HTTP_ERROR,
                f"Source returned HTTP {response.status_code}",
                final_url=url.url,
                http_status=response.status_code,
                content_type=content_type,
                declared_content_length=declared,
                bytes_received=len(content),
            )
        return FetchResult(
            final_url=url.url,
            redirect_count=0,
            http_status=response.status_code,
            headers=response.headers,
            content_type=content_type,
            declared_content_length=declared,
            bytes_received=len(content),
            content=content,
        )

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        max_bytes = get_settings().source_retrieval_max_bytes
        chunks: list[bytes] = []
        received = 0
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > max_bytes:
                raise SourceRetrievalError(
                    SourceRetrievalAttemptStatus.RESPONSE_TOO_LARGE,
                    "Response exceeded configured byte limit",
                    final_url=str(response.url),
                    http_status=response.status_code,
                    content_type=_bounded_optional(response.headers.get("content-type"), 255),
                    declared_content_length=_content_length(response.headers.get("content-length")),
                    bytes_received=received,
                )
            chunks.append(chunk)
        return b"".join(chunks)

    async def _assert_execution_access(
        self, db: AsyncSession, auth: AuthContext, execution_id: str
    ) -> None:
        result = await db.execute(
            select(ScrapingExecution.id).where(
                ScrapingExecution.id == execution_id,
                ScrapingExecution.organization_id == auth.org_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise NotFoundError("ScrapingExecution", execution_id)

    def _summary(
        self,
        attempt: ScrapingSourceRetrievalAttempt,
        document: ScrapingSourceDocument | None,
    ) -> SourceRetrievalSummary:
        return SourceRetrievalSummary(
            attempt_id=attempt.id,
            status=attempt.status.value,
            requested_url=attempt.requested_url,
            final_url=attempt.final_url,
            redirect_count=attempt.redirect_count,
            http_status=attempt.http_status,
            content_type=attempt.content_type,
            bytes_received=attempt.bytes_received,
            robots_status=attempt.robots_status.value if attempt.robots_status else None,
            failure_classification=attempt.failure_classification,
            safe_error_message=attempt.safe_error_message,
            document_id=document.id if document else None,
            content_sha256=document.content_sha256 if document else None,
        )


class _client_context:
    def __init__(self, client: Any) -> None:
        self.client = client

    async def __aenter__(self) -> Any:
        if hasattr(self.client, "__aenter__"):
            return await self.client.__aenter__()
        return self.client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if hasattr(self.client, "__aexit__"):
            await self.client.__aexit__(exc_type, exc, tb)
        elif hasattr(self.client, "aclose"):
            await self.client.aclose()


def _validate_hostname(hostname: str) -> None:
    if hostname in LOCALHOST_HOSTS or hostname.endswith(".localhost") or hostname in METADATA_HOSTS:
        raise SourceRetrievalError(SourceRetrievalAttemptStatus.UNSAFE_URL, "Hostname is not allowed")
    try:
        ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return
    if _unsafe_ip(ip):
        raise SourceRetrievalError(
            SourceRetrievalAttemptStatus.PRIVATE_OR_RESERVED_ADDRESS,
            "IP literal is not allowed",
        )


def _unsafe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in METADATA_IPS
    )


def _media_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower() or None


def _is_supported_content_type(media_type: str | None) -> bool:
    if not media_type:
        return False
    if media_type in SUPPORTED_CONTENT_TYPES:
        return True
    if media_type.startswith("text/") and media_type not in {"text/csv"}:
        return True
    if media_type.endswith("+json") or media_type.endswith("+xml"):
        return True
    return False


def _detect_charset(content_type: str | None) -> str | None:
    if not content_type:
        return None
    message: Message = Parser().parsestr(f"content-type: {content_type}\n\n")
    charset = message.get_content_charset()
    return charset[:80] if charset else None


def _content_length(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _bounded_optional(value: str | None, limit: int) -> str | None:
    return value[:limit] if value else None


def _safe_error(message: str) -> str:
    return str(message or "")[:MAX_ERROR_MESSAGE_LENGTH]


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = str(key).lower()
        if any(term in lowered for term in ("ip", "secret", "token", "key", "password")):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)[:80]] = value if not isinstance(value, str) else value[:500]
    return safe


source_retrieval_service = SourceRetrievalService()
