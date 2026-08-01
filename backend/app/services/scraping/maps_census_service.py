"""Standalone Google Places Maps census — independent of the scraping pipeline.

Runs a country-scoped grid of Places text searches, classifies each result
with an LLM, and validates any Google-provided website with the exact same
strict rules the scraping pipeline uses for official websites. Results live in
their own tables (``MapsCensusRun`` / ``MapsCensusCell`` / ``MapsPlace``) so
they can later be compared against a Scraping Council execution for the same
country without coupling the two pipelines together.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import (
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRun,
    MapsCensusStatus,
    MapsPlace,
    UsageKind,
)
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry
from app.schemas.api import MapsCensusRunDetail, MapsCensusRunSummary, MapsPlaceItem
from app.services.scraping.cost_tracking import record_scraping_llm
from app.services.scraping.countries import resolve_country
from app.services.scraping.facility_website_enrichment_service import (
    OfficialWebsiteCandidate,
    _homepage_url,
    _is_rejected_result,
    _is_weak_official_candidate,
    _safe_url,
    _tokens,
    build_official_website_query,
    select_official_website,
    website_needs_enrichment,
)
from app.services.scraping.maps_grid_planner import MapsGridPlanningError, maps_grid_planner
from app.services.scraping.maps_places_client import PlacesProviderError, create_places_client
from app.services.scraping.pexels_client import create_pexels_client
from app.services.scraping.search_providers import create_search_provider
from app.services.scraping.search_providers.base import (
    SearchProviderError,
    SearchProviderRequest,
    SearchProviderResult,
)

logger = logging.getLogger(__name__)

CLASSIFICATION_BATCH_TIMEOUT_SECONDS = 90.0
WEBSITE_LLM_BATCH_TIMEOUT_SECONDS = 90.0


class MapsRelevanceDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    place_id: str = Field(min_length=1, max_length=64)
    is_relevant: bool = False
    reason: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class MapsRelevancePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decisions: list[MapsRelevanceDecision] = Field(default_factory=list)


class MapsWebsiteDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    place_id: str = Field(min_length=1, max_length=64)
    url: str | None = Field(default=None, max_length=512)
    reason: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class MapsWebsitePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decisions: list[MapsWebsiteDecision] = Field(default_factory=list)


class MapsCensusService:
    async def create_run(
        self, db: AsyncSession, auth: AuthContext, country_code: str
    ) -> MapsCensusRunDetail:
        settings = get_settings()
        if not settings.google_places_enabled:
            raise ValidationError("Google Places Maps census is disabled.")
        country = resolve_country(country_code)
        run = MapsCensusRun(
            organization_id=auth.org_id,
            created_by=auth.user.id,
            country_code=country.code,
            country_name=country.name,
            status=MapsCensusStatus.QUEUED,
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()
        await self._enqueue(run_id)
        asyncio.create_task(self._fetch_hero_image(run_id, country.name))
        return await self.get_run(db, auth, run_id)

    async def _fetch_hero_image(self, run_id: str, country_name: str) -> None:
        """Best-effort Pexels lookup for a country hero photo — never raises,
        never blocks or fails the census run if Pexels is slow or unavailable.
        """
        try:
            client = create_pexels_client()
            url = await client.search_landscape(f"{country_name} landscape")
        except Exception:  # noqa: BLE001
            logger.warning("maps_census_hero_image_failed run_id=%s", run_id, exc_info=True)
            return
        if not url:
            return
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            run = await db.get(MapsCensusRun, run_id)
            if run is not None:
                run.hero_image_url = url
                await db.commit()

    async def list_runs(self, db: AsyncSession, auth: AuthContext) -> list[MapsCensusRunSummary]:
        rows = (
            await db.execute(
                select(MapsCensusRun)
                .where(MapsCensusRun.organization_id == auth.org_id)
                .order_by(MapsCensusRun.created_at.desc())
            )
        ).scalars().all()
        return [_run_summary(run) for run in rows]

    async def get_run(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCensusRunDetail:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        summary = _run_summary(run)
        return MapsCensusRunDetail(**summary.model_dump())

    async def list_places(
        self,
        db: AsyncSession,
        auth: AuthContext,
        run_id: str,
        *,
        relevant_only: bool = False,
        with_website_only: bool = False,
    ) -> list[MapsPlaceItem]:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        query = select(MapsPlace).where(MapsPlace.run_id == run_id)
        if relevant_only:
            query = query.where(MapsPlace.is_relevant.is_(True))
        if with_website_only:
            query = query.where(MapsPlace.official_website.is_not(None))
        rows = (await db.execute(query.order_by(MapsPlace.canonical_name))).scalars().all()
        return [_place_item(place) for place in rows]

    async def get_place_photo(
        self, db: AsyncSession, auth: AuthContext, run_id: str, place_id: str
    ) -> Path:
        """Return a local cache path for a facility photo, fetching it from Google
        Places once (and caching to disk) on first request. Never re-bills Google
        for a place we've already cached.
        """
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        place = await db.get(MapsPlace, place_id)
        if place is None or place.run_id != run_id:
            raise NotFoundError("Maps place", place_id)
        if not place.photo_reference:
            raise NotFoundError("Maps place photo", place_id)

        settings = get_settings()
        cache_dir = Path(settings.maps_census_photo_cache_dir)
        cache_path = cache_dir / f"{place.id}.jpg"
        if cache_path.exists():
            return cache_path

        if not settings.google_places_api_key:
            raise NotFoundError("Maps place photo", place_id)

        media_url = f"{settings.google_places_base_url}/{place.photo_reference}/media"
        try:
            async with httpx.AsyncClient(timeout=settings.google_places_timeout_seconds) as client:
                response = await client.get(
                    media_url,
                    params={"maxWidthPx": 480, "key": settings.google_places_api_key},
                )
        except httpx.HTTPError as exc:
            raise NotFoundError("Maps place photo", place_id) from exc

        if response.status_code != 200 or not response.content:
            raise NotFoundError("Maps place photo", place_id)

        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
        return cache_path

    async def _enqueue(self, run_id: str) -> None:
        await self._enqueue_job(
            "run_maps_census_job", run_id, inline_runner=lambda: run_maps_census_job({}, run_id)
        )

    async def _enqueue_refresh(self, run_id: str) -> None:
        await self._enqueue_job(
            "refresh_maps_census_websites_job",
            run_id,
            inline_runner=lambda: refresh_maps_census_websites_job({}, run_id),
        )

    async def _enqueue_job(self, job_name: str, run_id: str, *, inline_runner) -> None:
        settings = get_settings()
        inline = (
            settings.scraping_inline_execution
            if settings.scraping_inline_execution is not None
            else settings.environment == "development"
        )
        queued_on_redis = False
        if not inline:
            try:
                from urllib.parse import urlparse

                from arq import create_pool
                from arq.connections import RedisSettings

                parsed = urlparse(settings.redis_url)
                redis = await create_pool(
                    RedisSettings(
                        host=parsed.hostname or "localhost",
                        port=parsed.port or 6379,
                        database=int((parsed.path or "/0").lstrip("/") or "0"),
                        password=parsed.password,
                    )
                )
                await redis.enqueue_job(
                    job_name,
                    run_id,
                    _job_id=f"{job_name}:{run_id}:{uuid4().hex[:12]}",
                )
                await redis.close()
                queued_on_redis = True
            except Exception:
                logger.warning(
                    "maps_census_enqueue_redis_failed job=%s run_id=%s; falling back to inline",
                    job_name,
                    run_id,
                    exc_info=True,
                )
        if inline or not queued_on_redis:
            asyncio.create_task(inline_runner())

    async def delete_run(self, db: AsyncSession, auth: AuthContext, run_id: str) -> None:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        await db.delete(run)
        await db.commit()

    async def request_website_refresh(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCensusRunDetail:
        """Re-run the missing-website search fallback for a completed run — used to
        backfill facilities that didn't get an official website on the original pass.
        """
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        if run.status != MapsCensusStatus.COMPLETED:
            raise ValidationError("Only a completed Maps census run can refresh missing websites.")
        run.status = MapsCensusStatus.RUNNING
        run.heartbeat_at = datetime.now(UTC)
        await db.commit()
        await self._enqueue_refresh(run_id)
        return await self.get_run(db, auth, run_id)

    async def run_website_refresh(self, db: AsyncSession | None, *, run_id: str) -> dict[str, int]:
        session_factory = self._session_factory(db)
        await self._search_missing_websites(session_factory, run_id=run_id)
        await self._propagate_shared_websites(session_factory, run_id=run_id)
        await self._delete_unverified_places(session_factory, run_id=run_id)
        async with session_factory() as final_db:
            run = await final_db.get(MapsCensusRun, run_id)
            summary = {"places_with_website": 0}
            if run is not None:
                relevant_places = (
                    await final_db.execute(
                        select(MapsPlace).where(
                            MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(True)
                        )
                    )
                ).scalars().all()
                run.places_classified_relevant = len(relevant_places)
                run.places_with_website = sum(1 for p in relevant_places if p.official_website)
                run.status = MapsCensusStatus.COMPLETED
                run.completed_at = datetime.now(UTC)
                run.heartbeat_at = datetime.now(UTC)
                run.website_refresh_attempts += 1
                run.website_refresh_completed_at = datetime.now(UTC)
                summary["places_with_website"] = run.places_with_website
                await final_db.commit()
        return summary

    @staticmethod
    def _session_factory(db: AsyncSession | None):
        if db is not None:
            bind = db.bind
            if bind is None:
                bind = db.get_bind()
            return async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)
        from app.db.session import AsyncSessionLocal

        return AsyncSessionLocal

    async def run_census(self, db: AsyncSession | None, *, run_id: str) -> dict[str, int]:
        """Execute a Maps census run end to end with short-lived DB sessions.

        Never holds a connection across a Places/LLM network await — same
        discipline as ``facility_website_enrichment_service`` and
        ``facility_ai_cleanup_service``, to avoid starving the pool during
        long country runs.
        """
        session_factory = self._session_factory(db)
        settings = get_settings()

        async with session_factory() as start_db:
            run = await start_db.get(MapsCensusRun, run_id)
            if run is None:
                return {"error": 1}
            run.status = MapsCensusStatus.RUNNING
            run.started_at = datetime.now(UTC)
            run.heartbeat_at = datetime.now(UTC)
            await start_db.commit()
            country_code = run.country_code
            country_name = run.country_name
            org_id = run.organization_id
            owner_id = run.created_by

        try:
            cells = await maps_grid_planner.plan(
                country_code=country_code,
                country_name=country_name,
                max_cells=settings.maps_census_max_cells_per_run,
                org_id=org_id,
                user_id=owner_id,
                run_id=run_id,
            )
        except MapsGridPlanningError as exc:
            async with session_factory() as fail_db:
                run = await fail_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.status = MapsCensusStatus.FAILED
                    run.error_message = str(exc)[:2000]
                    run.completed_at = datetime.now(UTC)
                    await fail_db.commit()
            return {"error": 1}

        if not cells:
            async with session_factory() as fail_db:
                run = await fail_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.status = MapsCensusStatus.FAILED
                    run.error_message = "Grid planning returned no cells."
                    run.completed_at = datetime.now(UTC)
                    await fail_db.commit()
            return {"error": 1}

        async with session_factory() as cells_db:
            cell_ids: list[str] = []
            for cell in cells:
                row = MapsCensusCell(
                    run_id=run_id,
                    region_name=cell.region_name,
                    city_name=cell.city_name,
                    query_text=cell.query_text,
                )
                cells_db.add(row)
                await cells_db.flush()
                cell_ids.append(row.id)
            run = await cells_db.get(MapsCensusRun, run_id)
            run.cells_total = len(cell_ids)
            await cells_db.commit()

        client = create_places_client()
        summary = {"cells": len(cell_ids), "places_found": 0, "cell_failures": 0}
        if not client.is_configured():
            async with session_factory() as fail_db:
                run = await fail_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.status = MapsCensusStatus.FAILED
                    run.error_message = "Google Places API key is not configured."
                    run.completed_at = datetime.now(UTC)
                    await fail_db.commit()
            return summary

        for cell_id in cell_ids:
            async with session_factory() as heartbeat_db:
                run = await heartbeat_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await heartbeat_db.commit()

            async with session_factory() as cell_db:
                cell = await cell_db.get(MapsCensusCell, cell_id)
                if cell is None:
                    continue
                cell.status = MapsCensusCellStatus.IN_PROGRESS
                query_text = cell.query_text
                region_name = cell.region_name
                city_name = cell.city_name
                await cell_db.commit()

            try:
                results = await client.search_text(
                    query=query_text,
                    region_code=country_code,
                    max_results=settings.maps_census_max_places_per_cell,
                )
            except PlacesProviderError as exc:
                summary["cell_failures"] += 1
                async with session_factory() as cell_db:
                    cell = await cell_db.get(MapsCensusCell, cell_id)
                    if cell is not None:
                        cell.status = MapsCensusCellStatus.FAILED
                        cell.error_message = str(exc)[:2000]
                        cell.completed_at = datetime.now(UTC)
                        await cell_db.commit()
                continue

            async with session_factory() as write_db:
                new_places = 0
                for result in results:
                    existing = await write_db.scalar(
                        select(MapsPlace).where(
                            MapsPlace.run_id == run_id,
                            MapsPlace.google_place_id == result.google_place_id,
                        )
                    )
                    if existing is not None:
                        continue
                    write_db.add(
                        MapsPlace(
                            run_id=run_id,
                            google_place_id=result.google_place_id,
                            raw_name=result.raw_name,
                            canonical_name=result.raw_name,
                            place_types=result.place_types,
                            formatted_address=result.formatted_address,
                            city_name=city_name,
                            region_name=region_name,
                            latitude=result.latitude,
                            longitude=result.longitude,
                            international_phone_number=result.international_phone_number,
                            raw_website=result.website,
                            discovered_via_query=query_text,
                            photo_reference=result.photo_reference,
                        )
                    )
                    new_places += 1
                cell = await write_db.get(MapsCensusCell, cell_id)
                if cell is not None:
                    cell.status = MapsCensusCellStatus.COMPLETED
                    cell.places_found = len(results)
                    cell.completed_at = datetime.now(UTC)
                run = await write_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.cells_completed += 1
                    run.places_found += new_places
                await write_db.commit()
                summary["places_found"] += new_places

        classification_summary = await self._classify_pending(
            session_factory,
            run_id=run_id,
            country_code=country_code,
            country_name=country_name,
            org_id=org_id,
            user_id=owner_id,
        )
        summary.update(classification_summary)

        await self._validate_websites(session_factory, run_id=run_id)

        async with session_factory() as final_db:
            run = await final_db.get(MapsCensusRun, run_id)
            if run is not None:
                relevant_places = (
                    await final_db.execute(
                        select(MapsPlace).where(
                            MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(True)
                        )
                    )
                ).scalars().all()
                run.places_classified_relevant = len(relevant_places)
                run.places_with_website = sum(1 for p in relevant_places if p.official_website)
                run.status = MapsCensusStatus.COMPLETED
                run.completed_at = datetime.now(UTC)
                run.heartbeat_at = datetime.now(UTC)
                await final_db.commit()
        return summary

    async def _classify_pending(
        self,
        session_factory,
        *,
        run_id: str,
        country_code: str,
        country_name: str,
        org_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, int]:
        settings = get_settings()
        async with session_factory() as scan_db:
            pending = (
                await scan_db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(None)
                    )
                )
            ).scalars().all()
            pending_ids = [p.id for p in pending]
            await scan_db.commit()

        if not pending_ids:
            return {"classified": 0}

        model = get_model(settings.maps_census_model)
        provider = get_provider_registry().get_provider(model.provider)
        batch_size = max(int(settings.maps_census_classification_batch_size or 15), 1)
        classified = 0
        catalog_model_id = settings.maps_census_model

        for offset in range(0, len(pending_ids), batch_size):
            batch_ids = pending_ids[offset : offset + batch_size]
            async with session_factory() as batch_db:
                run = await batch_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                batch = (
                    await batch_db.execute(select(MapsPlace).where(MapsPlace.id.in_(batch_ids)))
                ).scalars().all()
                payloads = [
                    {
                        "place_id": place.id,
                        "name": place.raw_name,
                        "place_types": place.place_types,
                        "address": place.formatted_address,
                    }
                    for place in batch
                ]
                await batch_db.commit()

            decisions = await self._classify_batch(
                provider=provider,
                model_slug=model.provider_model,
                model_id=catalog_model_id,
                country_code=country_code,
                country_name=country_name,
                payloads=payloads,
                org_id=org_id,
                user_id=user_id,
                run_id=run_id,
                batch_index=offset // batch_size,
            )

            async with session_factory() as write_db:
                writable = (
                    await write_db.execute(select(MapsPlace).where(MapsPlace.id.in_(batch_ids)))
                ).scalars().all()
                by_id = {place.id: place for place in writable}
                for decision in decisions:
                    place = by_id.get(decision.place_id)
                    if place is None:
                        continue
                    place.is_relevant = decision.is_relevant
                    place.relevance_reason = decision.reason[:300]
                    place.confidence_score = decision.confidence
                    classified += 1
                await write_db.commit()

        return {"classified": classified}

    async def _classify_batch(
        self,
        *,
        provider: Any,
        model_slug: str,
        model_id: str,
        country_code: str,
        country_name: str,
        payloads: list[dict[str, Any]],
        org_id: str | None = None,
        user_id: str | None = None,
        run_id: str | None = None,
        batch_index: int = 0,
        db: AsyncSession | None = None,
    ) -> list[MapsRelevanceDecision]:
        prompt = get_prompt_engine().render(
            "scraping/maps_relevance_classifier.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            places_json=json.dumps(payloads, ensure_ascii=True),
        )
        idem = f"maps-classify:{run_id or 'unknown'}:{batch_index}"
        try:
            response = await asyncio.wait_for(
                provider.complete(
                    system=(
                        "You return strict JSON relevance decisions for candidate rehab/addiction/"
                        "psychiatric facility places found via Google Places search."
                    ),
                    user=prompt,
                    model=model_slug,
                    max_tokens=3000,
                ),
                timeout=CLASSIFICATION_BATCH_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_census_classification_batch_failed count=%s error=%s",
                len(payloads),
                exc,
            )
            await record_scraping_llm(
                db,
                org_id=org_id,
                user_id=user_id,
                model_id=model_id,
                kind=UsageKind.CLASSIFICATION,
                operation="maps_classify",
                idempotency_key=f"{idem}:failed",
                failed=True,
                error_code="maps_classify_failed",
            )
            return [
                MapsRelevanceDecision(
                    place_id=item["place_id"], is_relevant=False, reason="classification_failed"
                )
                for item in payloads
            ]
        await record_scraping_llm(
            db,
            org_id=org_id,
            user_id=user_id,
            model_id=model_id,
            kind=UsageKind.CLASSIFICATION,
            operation="maps_classify",
            idempotency_key=idem,
            response=response,
            metadata={"place_count": len(payloads)},
        )
        try:
            raw = LLMProvider.parse_json_response(response.text)
            plan = MapsRelevancePlan.model_validate(_normalize_relevance_payload(raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_census_classification_parse_failed count=%s error=%s",
                len(payloads),
                exc,
            )
            return [
                MapsRelevanceDecision(
                    place_id=item["place_id"], is_relevant=False, reason="classification_failed"
                )
                for item in payloads
            ]
        by_id = {d.place_id: d for d in plan.decisions if d.place_id}
        return [
            by_id.get(item["place_id"])
            or MapsRelevanceDecision(
                place_id=item["place_id"], is_relevant=False, reason="missing_decision"
            )
            for item in payloads
        ]

    async def _validate_websites(self, session_factory, *, run_id: str) -> None:
        """Trust a Places-provided website when it passes strict validation; otherwise
        fall back to a name+geography search for any relevant place still missing one.
        """
        settings = get_settings()
        async with session_factory() as db:
            places = (
                await db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                        MapsPlace.raw_website.is_not(None),
                    )
                )
            ).scalars().all()
            for place in places:
                if not website_needs_enrichment(place.raw_website):
                    place.official_website = place.raw_website
                    place.website_source = "places"
            await db.commit()

        if settings.maps_census_website_search_enabled:
            await self._search_missing_websites(session_factory, run_id=run_id)
        await self._propagate_shared_websites(session_factory, run_id=run_id)
        await self._delete_unverified_places(session_factory, run_id=run_id)

    async def _delete_unverified_places(self, session_factory, *, run_id: str) -> int:
        """Permanently remove relevant facilities lacking a verified official site."""
        async with session_factory() as db:
            result = await db.execute(
                delete(MapsPlace).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.is_relevant.is_(True),
                    MapsPlace.official_website.is_(None),
                )
            )
            await db.commit()
            return int(result.rowcount or 0)

    async def _drop_untrusted_websites(self, session_factory, *, run_id: str) -> int:
        """Re-validate stored websites so blocklist changes apply retroactively.

        A directory that slipped through earlier (e.g. a B2B listing site added to
        the blocklist later) is cleared here, which also re-queues that place for
        the search pass below.
        """
        async with session_factory() as db:
            places = (
                await db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                        MapsPlace.official_website.is_not(None),
                    )
                )
            ).scalars().all()
            dropped = 0
            for place in places:
                if website_needs_enrichment(place.official_website):
                    place.official_website = None
                    place.website_source = None
                    dropped += 1
            if dropped:
                await db.commit()
            return dropped

    async def _propagate_shared_websites(self, session_factory, *, run_id: str) -> None:
        """Same-name multi-location facilities share one official website when unambiguous.

        If every known website in a canonical-name group is the same URL, copy it to
        sibling locations that are still missing one. If the group already has
        conflicting websites (different municipal facilities that happen to share a
        generic name), leave each location alone.
        """
        async with session_factory() as db:
            places = (
                await db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                    )
                )
            ).scalars().all()
            by_name: dict[str, list[MapsPlace]] = {}
            for place in places:
                key = (place.canonical_name or "").strip().casefold()
                if not key:
                    continue
                by_name.setdefault(key, []).append(place)

            changed = False
            for group in by_name.values():
                if len(group) < 2:
                    continue
                known = {
                    (p.official_website or "").strip()
                    for p in group
                    if (p.official_website or "").strip()
                }
                if len(known) != 1:
                    continue
                shared_url = next(iter(known))
                donor = next(p for p in group if (p.official_website or "").strip() == shared_url)
                for place in group:
                    if place.official_website:
                        continue
                    place.official_website = shared_url
                    place.website_source = donor.website_source or "search"
                    changed = True
            if changed:
                await db.commit()

    async def _search_missing_websites(self, session_factory, *, run_id: str) -> None:
        settings = get_settings()
        limit = max(1, settings.maps_census_website_search_max_places_per_run)
        await self._drop_untrusted_websites(session_factory, run_id=run_id)
        async with session_factory() as scan_db:
            run = await scan_db.get(MapsCensusRun, run_id)
            country_name = run.country_name if run is not None else ""
            country_code = run.country_code if run is not None else ""
            org_id = run.organization_id if run is not None else None
            owner_id = run.created_by if run is not None else None
            pending = (
                await scan_db.execute(
                    select(MapsPlace)
                    .where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                        MapsPlace.official_website.is_(None),
                    )
                    .order_by(MapsPlace.canonical_name)
                    .limit(limit)
                )
            ).scalars().all()
            pending_items = [
                {
                    "id": place.id,
                    "name": place.canonical_name,
                    "city": place.city_name or place.region_name,
                    "address": place.formatted_address,
                    "phone": place.international_phone_number,
                }
                for place in pending
            ]
            await scan_db.commit()

        if not pending_items:
            return

        provider = create_search_provider()
        llm_queue: list[dict[str, Any]] = []
        for item in pending_items:
            async with session_factory() as heartbeat_db:
                run = await heartbeat_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await heartbeat_db.commit()

            results = await self._search_website_candidates(
                provider,
                item=item,
                country_code=country_code,
                country_name=country_name,
            )
            if not results:
                continue

            selected = select_official_website(
                facility_name=item["name"],
                city=item["city"],
                country_name=country_name,
                results=results,
            )
            if selected is None:
                selected = _match_official_website_by_address(
                    address=item["address"],
                    city=item["city"],
                    country_name=country_name,
                    results=results,
                )
            if selected is not None:
                async with session_factory() as write_db:
                    place = await write_db.get(MapsPlace, item["id"])
                    if place is not None and place.official_website is None:
                        place.official_website = selected.url
                        place.website_source = "search"
                    await write_db.commit()
                continue

            if settings.maps_census_website_llm_enabled:
                llm_queue.append({**item, "candidates": results})

        if not llm_queue:
            return

        model = get_model(settings.maps_census_website_llm_model)
        llm_provider = get_provider_registry().get_provider(model.provider)
        batch_size = max(1, settings.maps_census_website_llm_batch_size)
        for offset in range(0, len(llm_queue), batch_size):
            batch = llm_queue[offset : offset + batch_size]
            async with session_factory() as heartbeat_db:
                run = await heartbeat_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await heartbeat_db.commit()

            decisions = await self._select_websites_llm_batch(
                provider=llm_provider,
                model_slug=model.provider_model,
                model_id=settings.maps_census_website_llm_model,
                country_code=country_code,
                country_name=country_name,
                batch=batch,
                org_id=org_id,
                user_id=owner_id,
                run_id=run_id,
                batch_index=offset // batch_size,
            )
            by_id = {d.place_id: d for d in decisions}
            for item in batch:
                decision = by_id.get(item["id"])
                url = _accepted_llm_website_url(
                    decision.url if decision is not None else None,
                    candidates=item["candidates"],
                )
                if not url:
                    continue
                async with session_factory() as write_db:
                    place = await write_db.get(MapsPlace, item["id"])
                    if place is not None and place.official_website is None:
                        place.official_website = url
                        place.website_source = "search"
                    await write_db.commit()

    async def _search_website_candidates(
        self,
        provider: Any,
        *,
        item: dict[str, Any],
        country_code: str,
        country_name: str,
    ) -> list[SearchProviderResult]:
        settings = get_settings()
        merged: list[SearchProviderResult] = []
        seen_urls: set[str] = set()
        for query in _maps_website_search_queries(
            name=item["name"],
            city=item["city"],
            country_name=country_name,
            address=item["address"],
        ):
            try:
                results = await asyncio.wait_for(
                    provider.search(
                        SearchProviderRequest(
                            query=query,
                            country_code=country_code,
                            search_language="en",
                            result_limit=settings.facility_website_enrichment_results_per_facility,
                            metadata={
                                "purpose": "maps_census_official_website",
                                "place_id": item["id"],
                            },
                        )
                    ),
                    timeout=settings.facility_website_enrichment_timeout_seconds,
                )
            except (TimeoutError, SearchProviderError):
                continue
            for result in results:
                key = (result.url or "").strip().casefold()
                if not key or key in seen_urls:
                    continue
                seen_urls.add(key)
                merged.append(
                    SearchProviderResult(
                        rank=len(merged) + 1,
                        url=result.url,
                        title=result.title,
                        snippet=result.snippet,
                    )
                )
            # First non-empty query is enough — further variants are only needed
            # when the previous query returned nothing (e.g. quoted transliteration).
            if merged:
                break
        return merged

    async def _select_websites_llm_batch(
        self,
        *,
        provider: Any,
        model_slug: str,
        model_id: str,
        country_code: str,
        country_name: str,
        batch: list[dict[str, Any]],
        org_id: str | None = None,
        user_id: str | None = None,
        run_id: str | None = None,
        batch_index: int = 0,
    ) -> list[MapsWebsiteDecision]:
        payloads = []
        for item in batch:
            candidates = []
            for result in item.get("candidates") or []:
                if _is_rejected_result(result):
                    continue
                candidates.append(
                    {
                        "url": result.url,
                        "title": (result.title or "")[:200],
                        "snippet": (result.snippet or "")[:240],
                    }
                )
            payloads.append(
                {
                    "place_id": item["id"],
                    "name": item["name"],
                    "city": item.get("city"),
                    "address": item.get("address"),
                    "phone": item.get("phone"),
                    "candidates": candidates[:8],
                }
            )
        if not any(p["candidates"] for p in payloads):
            return [
                MapsWebsiteDecision(place_id=p["place_id"], url=None, reason="no_candidates")
                for p in payloads
            ]

        prompt = get_prompt_engine().render(
            "scraping/maps_website_selector.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            facilities_json=json.dumps(payloads, ensure_ascii=False),
        )
        idem = f"maps-website:{run_id or 'unknown'}:{batch_index}"
        try:
            response = await asyncio.wait_for(
                provider.complete(
                    system=(
                        "You return strict JSON picking official facility homepage URLs "
                        "from provided search candidates only."
                    ),
                    user=prompt,
                    model=model_slug,
                    max_tokens=2500,
                ),
                timeout=WEBSITE_LLM_BATCH_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_census_website_llm_batch_failed count=%s error=%s",
                len(payloads),
                exc,
            )
            await record_scraping_llm(
                None,
                org_id=org_id,
                user_id=user_id,
                model_id=model_id,
                kind=UsageKind.CLASSIFICATION,
                operation="maps_website_recovery",
                idempotency_key=f"{idem}:failed",
                failed=True,
                error_code="maps_website_recovery_failed",
            )
            return [
                MapsWebsiteDecision(place_id=p["place_id"], url=None, reason="llm_failed")
                for p in payloads
            ]

        await record_scraping_llm(
            None,
            org_id=org_id,
            user_id=user_id,
            model_id=model_id,
            kind=UsageKind.CLASSIFICATION,
            operation="maps_website_recovery",
            idempotency_key=idem,
            response=response,
            metadata={"place_count": len(payloads)},
        )
        try:
            raw = LLMProvider.parse_json_response(response.text)
            plan = MapsWebsitePlan.model_validate(_normalize_website_payload(raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_census_website_llm_parse_failed count=%s error=%s",
                len(payloads),
                exc,
            )
            return [
                MapsWebsiteDecision(place_id=p["place_id"], url=None, reason="llm_failed")
                for p in payloads
            ]

        by_id = {d.place_id: d for d in plan.decisions}
        return [
            by_id.get(p["place_id"])
            or MapsWebsiteDecision(place_id=p["place_id"], url=None, reason="missing_decision")
            for p in payloads
        ]


_LETTER_DIGIT_BOUNDARY = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", re.UNICODE)


def _split_glued_house_numbers(value: str) -> str:
    return _LETTER_DIGIT_BOUNDARY.sub(" ", value)


def _maps_website_search_queries(
    *,
    name: str,
    city: str | None,
    country_name: str,
    address: str | None,
) -> list[str]:
    """Ordered Serper queries for Maps missing-website backfill.

    The shared quoted ``"name" … official website`` query is great for Latin
    facility names, but Google Places often returns Latin *transliterations*
    of Cyrillic names — quoting those yields zero hits. Fall back to an
    unquoted name query, then a street-address query that keeps the native
    script Google left in ``formatted_address``.
    """
    queries: list[str] = []
    quoted = build_official_website_query(name=name, city=city, country_name=country_name)
    if quoted:
        queries.append(quoted)

    geography = " ".join(part for part in (city, country_name) if part)
    unquoted = f"{name.strip()} {geography} official website".strip()[:240]
    if unquoted and unquoted not in queries:
        queries.append(unquoted)

    street = _street_query_fragment(address)
    if street:
        city_part = (city or "").strip()
        address_query = f"{street} {city_part} official website".strip()[:240]
        if address_query and address_query not in queries:
            queries.append(address_query)
    return queries


def _street_query_fragment(address: str | None) -> str | None:
    if not address:
        return None
    # Prefer the street/house portion before the first comma — that's where
    # Google usually keeps native-script street names for Belarus etc.
    head = address.split(",", 1)[0].strip()
    spaced = _split_glued_house_numbers(head).strip()
    tokens = _address_tokens(spaced)
    if len(tokens) < 2:
        return None
    return spaced[:160] or None


def _address_tokens(address: str | None) -> set[str]:
    if not address:
        return set()
    # Google's formatted_address often glues the house number onto the street
    # name with no separator (e.g. "Гастелло16"), which would otherwise
    # tokenize as one blob that never matches a real site's "ул. Гастелло, 16".
    # Insert a boundary at letter/digit transitions so the number tokenizes
    # separately — it's often the second signal alongside a street name that
    # confirms a real match. _tokens() already drops 1-char tokens and stopwords.
    return _tokens(_split_glued_house_numbers(address))


def _match_official_website_by_address(
    *,
    address: str | None,
    city: str | None,
    country_name: str,
    results: list[SearchProviderResult],
) -> OfficialWebsiteCandidate | None:
    """Fallback matcher for when Places' transliterated name can't be matched
    against native-script site content — matches on address tokens instead
    (street name, house number), which Google often leaves partially untransliterated.
    """
    address_tokens = _address_tokens(address)
    if len(address_tokens) < 2:
        return None
    scored: list[OfficialWebsiteCandidate] = []
    for item in results:
        if _is_rejected_result(item):
            continue
        parsed = _safe_url(item.url)
        if parsed is None:
            continue
        blob_tokens = _tokens(f"{item.title} {item.snippet}")
        overlap = address_tokens & blob_tokens
        # Require at least two distinct address tokens (e.g. street name + house
        # number) so a bare city-name mention alone can't count as a match.
        if len(overlap) < 2:
            continue
        host_tokens = _tokens(parsed.hostname or "")
        host_coverage = len(address_tokens & host_tokens) / len(address_tokens)
        if _is_weak_official_candidate(parsed, host_coverage=host_coverage):
            continue
        blob = f"{item.title} {item.snippet}".casefold()
        score = len(overlap) * 12
        if country_name.casefold() in blob:
            score += 8
        if city and city.casefold() in blob:
            score += 5
        if parsed.path in {"", "/"}:
            score += 10
        score += max(0, 6 - max(item.rank, 1))
        if score < 35:
            continue
        scored.append(
            OfficialWebsiteCandidate(
                url=_homepage_url(parsed),
                score=score,
                source_url=item.url,
                title=item.title[:500],
            )
        )
    if not scored:
        return None
    scored.sort(key=lambda candidate: (-candidate.score, candidate.url))
    if len(scored) > 1 and scored[0].score - scored[1].score < 8:
        return None
    return scored[0]


def _run_summary(run: MapsCensusRun) -> MapsCensusRunSummary:
    return MapsCensusRunSummary(
        id=run.id,
        country_code=run.country_code,
        country_name=run.country_name,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        error_message=run.error_message,
        cells_total=run.cells_total,
        cells_completed=run.cells_completed,
        places_found=run.places_found,
        places_classified_relevant=run.places_classified_relevant,
        places_with_website=run.places_with_website,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        hero_image_url=run.hero_image_url,
    )


def _place_item(place: MapsPlace) -> MapsPlaceItem:
    return MapsPlaceItem(
        id=place.id,
        google_place_id=place.google_place_id,
        canonical_name=place.canonical_name,
        place_types=place.place_types or [],
        formatted_address=place.formatted_address,
        city_name=place.city_name,
        region_name=place.region_name,
        latitude=place.latitude,
        longitude=place.longitude,
        international_phone_number=place.international_phone_number,
        raw_website=place.raw_website,
        official_website=place.official_website,
        website_source=place.website_source,
        is_relevant=place.is_relevant,
        relevance_reason=place.relevance_reason,
        confidence_score=float(place.confidence_score) if place.confidence_score is not None else None,
        discovered_via_query=place.discovered_via_query,
        has_photo=bool(place.photo_reference),
    )


def _normalize_website_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("decisions") or raw.get("results") or raw.get("websites") or []
    else:
        items = []
    if not isinstance(items, list):
        items = []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("website") or item.get("official_website")
        if isinstance(url, str):
            url = url.strip() or None
        else:
            url = None
        normalized.append(
            {
                "place_id": item.get("place_id") or item.get("id"),
                "url": url,
                "reason": item.get("reason") or item.get("explanation") or "",
                "confidence": item.get("confidence", 0.5),
            }
        )
    return {"decisions": normalized}


def _accepted_llm_website_url(
    url: str | None,
    *,
    candidates: list[SearchProviderResult],
) -> str | None:
    """Accept an LLM pick only if it maps to a non-blocked candidate host homepage."""
    if not url:
        return None
    parsed = _safe_url(url)
    if parsed is None:
        return None
    host = (parsed.hostname or "").casefold()
    if not host:
        return None
    allowed_hosts = set()
    for candidate in candidates:
        if _is_rejected_result(candidate):
            continue
        candidate_parsed = _safe_url(candidate.url)
        if candidate_parsed is None or not candidate_parsed.hostname:
            continue
        allowed_hosts.add(candidate_parsed.hostname.casefold())
    if host not in allowed_hosts:
        return None
    homepage = _homepage_url(parsed)
    probe = SearchProviderResult(rank=1, url=homepage, title="", snippet="")
    if _is_rejected_result(probe):
        return None
    return homepage


def _normalize_relevance_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("decisions") or raw.get("places") or raw.get("results") or []
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
                "place_id": item.get("place_id") or item.get("id"),
                "is_relevant": bool(item.get("is_relevant") or item.get("relevant")),
                "reason": item.get("reason") or item.get("explanation") or "",
                "confidence": item.get("confidence", 0.5),
            }
        )
    return {"decisions": normalized}


maps_census_service = MapsCensusService()


async def run_maps_census_job(ctx: dict, run_id: str) -> None:
    """ARQ entrypoint: run a queued Maps census to completion."""
    del ctx
    logger.info("maps_census_job_entered", extra={"run_id": run_id})
    try:
        await maps_census_service.run_census(None, run_id=run_id)
    except Exception:  # noqa: BLE001
        logger.exception("maps_census_job_failed", extra={"run_id": run_id})
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            run = await db.get(MapsCensusRun, run_id)
            if run is not None and run.status != MapsCensusStatus.COMPLETED:
                run.status = MapsCensusStatus.FAILED
                run.error_message = "Unexpected error during Maps census execution."
                run.completed_at = datetime.now(UTC)
                await db.commit()


async def refresh_maps_census_websites_job(ctx: dict, run_id: str) -> None:
    """ARQ entrypoint: backfill missing official websites for a completed run."""
    del ctx
    logger.info("maps_census_refresh_websites_job_entered", extra={"run_id": run_id})
    try:
        await maps_census_service.run_website_refresh(None, run_id=run_id)
    except Exception:  # noqa: BLE001
        logger.exception("maps_census_refresh_websites_job_failed", extra={"run_id": run_id})
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            run = await db.get(MapsCensusRun, run_id)
            if run is not None and run.status != MapsCensusStatus.COMPLETED:
                run.status = MapsCensusStatus.FAILED
                run.error_message = "Unexpected error while refreshing missing websites."
                run.completed_at = datetime.now(UTC)
                await db.commit()


async def recover_maps_census_runs(ctx: dict) -> None:
    """Requeue Maps census runs whose worker died mid-flight (stale heartbeat)."""
    del ctx
    from datetime import timedelta

    from app.db.session import AsyncSessionLocal

    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    async with AsyncSessionLocal() as db:
        stale = (
            await db.execute(
                select(MapsCensusRun).where(
                    MapsCensusRun.status == MapsCensusStatus.RUNNING,
                    (MapsCensusRun.heartbeat_at.is_(None)) | (MapsCensusRun.heartbeat_at < cutoff),
                )
            )
        ).scalars().all()
        run_ids = [run.id for run in stale]
        await db.commit()

    for run_id in run_ids:
        logger.warning("maps_census_recovering_stale_run", extra={"run_id": run_id})
        asyncio.create_task(run_maps_census_job({}, run_id))


async def auto_refresh_maps_census_websites(ctx: dict) -> None:
    """Periodically retry the missing-website search for completed runs that still
    have relevant facilities without an official website — fully automatic, no
    manual "find missing websites" click required.
    """
    del ctx
    settings = get_settings()
    if not settings.maps_census_auto_website_refresh_enabled:
        return
    from datetime import timedelta

    from app.db.session import AsyncSessionLocal

    cooldown = timedelta(hours=max(1, settings.maps_census_auto_website_refresh_cooldown_hours))
    max_attempts = max(1, settings.maps_census_auto_website_refresh_max_attempts)
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as db:
        candidates = (
            await db.execute(
                select(MapsCensusRun).where(
                    MapsCensusRun.status == MapsCensusStatus.COMPLETED,
                    MapsCensusRun.places_classified_relevant > MapsCensusRun.places_with_website,
                    MapsCensusRun.website_refresh_attempts < max_attempts,
                )
            )
        ).scalars().all()
        due_run_ids: list[str] = []
        for run in candidates:
            last_attempt = run.website_refresh_completed_at or run.completed_at
            if last_attempt is not None and last_attempt.tzinfo is None:
                last_attempt = last_attempt.replace(tzinfo=UTC)
            if last_attempt is not None and now - last_attempt < cooldown:
                continue
            run.status = MapsCensusStatus.RUNNING
            run.heartbeat_at = now
            due_run_ids.append(run.id)
        await db.commit()

    for run_id in due_run_ids:
        logger.info("maps_census_auto_refresh_websites", extra={"run_id": run_id})
        asyncio.create_task(refresh_maps_census_websites_job({}, run_id))
