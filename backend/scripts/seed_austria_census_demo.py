"""Seed the Austria full-census demo scrape into a target database.

Loads ``scripts/fixtures/austria_census_demo.json`` into the Acme org so the
UI can show a completed execution with published facilities.

Usage (from ``backend/``)::

    # local sqlite
    python -m scripts.seed_austria_census_demo

    # production / remote
    DATABASE_URL=postgresql+asyncpg://... python -m scripts.seed_austria_census_demo

The script is idempotent: re-running replaces the previous demo mission graph
tagged with the same demo marker.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select

from app.db.models import (
    Organization,
    RehabilitationFacility,
    RehabilitationFacilityAlias,
    RehabilitationFacilityContact,
    RehabilitationFacilityLocation,
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingMission,
    ScrapingMissionStatus,
    ScrapingRun,
    ScrapingRunStatus,
    User,
)
from app.db.session import AsyncSessionLocal, engine
from app.db.base import Base
from scripts.seed import DEMO_EMAIL, DEMO_ORG_SLUG, seed as ensure_base_seed

DEMO_MARKER = "austria-full-census-demo-v1"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "austria_census_demo.json"
PRESENTATION_TITLE = "Austria rehab facilities - Full census"


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip().replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _load_fixture() -> dict[str, Any]:
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(f"Missing fixture: {FIXTURE_PATH}")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


async def _resolve_org_and_user(db) -> tuple[Organization, User]:
    org = (
        await db.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    ).scalar_one_or_none()
    if org is None:
        raise RuntimeError(
            f"Organization slug={DEMO_ORG_SLUG!r} not found. Run scripts.seed first."
        )
    user = (await db.execute(select(User).where(User.email == DEMO_EMAIL))).scalar_one_or_none()
    if user is None:
        raise RuntimeError(f"Demo user {DEMO_EMAIL!r} not found. Run scripts.seed first.")
    return org, user


async def _delete_existing_demo(db, org_id: str) -> int:
    """Remove prior demo missions (by marker in country_profile or presentation title)."""
    result = await db.execute(
        select(ScrapingExecution).where(ScrapingExecution.organization_id == org_id)
    )
    mission_ids: set[str] = set()
    for execution in result.scalars().all():
        profile = execution.country_profile_json or {}
        if profile.get("demo_marker") == DEMO_MARKER:
            mission_ids.add(execution.mission_id)
    mission_result = await db.execute(
        select(ScrapingMission).where(
            ScrapingMission.org_id == org_id,
            ScrapingMission.title == PRESENTATION_TITLE,
        )
    )
    for mission in mission_result.scalars().all():
        mission_ids.add(mission.id)
    for mission_id in mission_ids:
        await db.execute(delete(ScrapingMission).where(ScrapingMission.id == mission_id))
    return len(mission_ids)


async def seed_austria_demo(*, ensure_base: bool = True) -> dict[str, Any]:
    if ensure_base:
        await ensure_base_seed()

    fixture = _load_fixture()
    mission_src = fixture["mission"]
    blueprint_src = fixture["blueprints"][0]
    run_src = fixture["runs"][0]
    execution_src = fixture["execution"]
    facilities_src = fixture["facilities"]
    contacts_src = fixture.get("contacts") or []
    locations_src = fixture.get("locations") or []
    aliases_src = fixture.get("aliases") or []

    # Fresh IDs so local and prod never collide across environments.
    mission_id = str(uuid4())
    blueprint_id = str(uuid4())
    run_id = str(uuid4())
    execution_id = str(uuid4())
    facility_id_map = {row["id"]: str(uuid4()) for row in facilities_src}

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        org, user = await _resolve_org_and_user(db)
        removed = await _delete_existing_demo(db, org.id)
        await db.flush()

        model_set_id = mission_src.get("model_set_id") or "balanced"
        profile = dict(execution_src.get("country_profile_json") or {})
        profile["demo_marker"] = DEMO_MARKER
        profile["seeded_from_execution_id"] = fixture.get("source_execution_id")
        profile["seeded_at"] = datetime.now(UTC).isoformat()

        mission = ScrapingMission(
            id=mission_id,
            org_id=org.id,
            created_by=user.id,
            project_id=None,
            model_set_id=model_set_id,
            title=PRESENTATION_TITLE,
            original_prompt=mission_src.get("original_prompt")
            or "Full census of rehabilitation facilities in Austria.",
            country_code=mission_src.get("country_code") or "AT",
            country_name=mission_src.get("country_name") or "Austria",
            status=ScrapingMissionStatus.APPROVED,
            active_blueprint_id=None,
        )
        db.add(mission)
        await db.flush()

        approved_at = _parse_dt(blueprint_src.get("approved_at")) or datetime.now(UTC)
        blueprint = ScrapingBlueprint(
            id=blueprint_id,
            mission_id=mission.id,
            version=int(blueprint_src.get("version") or 1),
            status=ScrapingBlueprintStatus.APPROVED,
            blueprint_json=blueprint_src.get("blueprint_json") or {},
            display_name=blueprint_src.get("display_name") or "Austria full census",
            model_set_id=blueprint_src.get("model_set_id") or model_set_id,
            judge_model_id=blueprint_src.get("judge_model_id") or "gpt-4.1",
            approved_by=user.id,
            approved_at=approved_at,
        )
        db.add(blueprint)
        await db.flush()
        mission.active_blueprint_id = blueprint.id

        started_at = _parse_dt(execution_src.get("started_at")) or datetime.now(UTC)
        completed_at = _parse_dt(execution_src.get("completed_at")) or datetime.now(UTC)
        run = ScrapingRun(
            id=run_id,
            organization_id=org.id,
            mission_id=mission.id,
            blueprint_id=blueprint.id,
            model_set_id=run_src.get("model_set_id") or model_set_id,
            status=ScrapingRunStatus.COMPLETED,
            recommended_agent_count=run_src.get("recommended_agent_count"),
            planner_model_id=run_src.get("planner_model_id"),
            planner_rationale=run_src.get("planner_rationale"),
            plan_json=run_src.get("plan_json"),
            started_at=started_at,
            completed_at=completed_at,
        )
        db.add(run)
        await db.flush()

        execution = ScrapingExecution(
            id=execution_id,
            organization_id=org.id,
            mission_id=mission.id,
            blueprint_id=blueprint.id,
            team_plan_id=run.id,
            execution_type=execution_src.get("execution_type") or "initial_full_country",
            mode=execution_src.get("mode") or "full_census",
            status=ScrapingExecutionStatus.COMPLETED,
            country_code=execution_src.get("country_code") or "AT",
            country_name=execution_src.get("country_name") or "Austria",
            country_profile_json=profile,
            started_at=started_at,
            completed_at=completed_at,
            heartbeat_at=completed_at,
            sources_discovered=int(execution_src.get("sources_discovered") or 0),
            documents_found=int(execution_src.get("documents_found") or 0),
            records_extracted=int(execution_src.get("records_extracted") or 0),
            records_verified=int(
                execution_src.get("records_verified") or len(facilities_src)
            ),
            duplicates_detected=int(execution_src.get("duplicates_detected") or 0),
            blocked_sources=int(execution_src.get("blocked_sources") or 0),
            coverage_debt=int(execution_src.get("coverage_debt") or 0),
        )
        db.add(execution)
        await db.flush()

        for row in facilities_src:
            new_id = facility_id_map[row["id"]]
            db.add(
                RehabilitationFacility(
                    id=new_id,
                    execution_id=execution.id,
                    organization_id=org.id,
                    stable_key=row["stable_key"],
                    canonical_name=row["canonical_name"],
                    original_language_name=row.get("original_language_name"),
                    description=row.get("description"),
                    facility_type=row.get("facility_type") or "unknown",
                    organization_type=row.get("organization_type") or "not_classified",
                    operational_status=row.get("operational_status") or "not_verified",
                    country_code=row.get("country_code") or "AT",
                    country_name=row.get("country_name") or "Austria",
                    primary_region=row.get("primary_region"),
                    primary_city=row.get("primary_city"),
                    primary_address=row.get("primary_address"),
                    latitude=row.get("latitude"),
                    longitude=row.get("longitude"),
                    primary_website=row.get("primary_website"),
                    verification_status=row.get("verification_status")
                    or "verified_from_staging",
                    confidence_score=float(row.get("confidence_score") or 0.5),
                    duplicate_status=row.get("duplicate_status") or "unique",
                    human_review_status=row.get("human_review_status") or "required",
                    is_mock=_as_bool(row.get("is_mock"), False),
                    last_verified_at=_parse_dt(row.get("last_verified_at")),
                )
            )
        await db.flush()

        website_contact_facility_ids: set[str] = set()
        for row in contacts_src:
            facility_id = facility_id_map.get(row["facility_id"])
            if not facility_id:
                continue
            contact_type = row.get("contact_type") or "other"
            if contact_type == "website":
                website_contact_facility_ids.add(facility_id)
            db.add(
                RehabilitationFacilityContact(
                    id=str(uuid4()),
                    facility_id=facility_id,
                    contact_type=contact_type,
                    label=row.get("label"),
                    value=row["value"],
                    normalized_value=row.get("normalized_value"),
                    is_primary=_as_bool(row.get("is_primary"), False),
                    available_24_7=_as_bool(row.get("available_24_7"), False),
                    verification_status=row.get("verification_status")
                    or "verified_from_staging",
                    confidence_score=float(row.get("confidence_score") or 0.5),
                    is_mock=_as_bool(row.get("is_mock"), False),
                )
            )

        # Ensure every seeded website is also a contact so the dossier Contacts tab shows it.
        for row in facilities_src:
            website = (row.get("primary_website") or "").strip()
            if not website:
                continue
            facility_id = facility_id_map[row["id"]]
            if facility_id in website_contact_facility_ids:
                continue
            db.add(
                RehabilitationFacilityContact(
                    id=str(uuid4()),
                    facility_id=facility_id,
                    contact_type="website",
                    label=None,
                    value=website,
                    normalized_value=website,
                    is_primary=True,
                    available_24_7=False,
                    verification_status="enriched_for_demo",
                    confidence_score=float(row.get("confidence_score") or 0.6),
                    is_mock=False,
                )
            )

        for row in locations_src:
            facility_id = facility_id_map.get(row["facility_id"])
            if not facility_id:
                continue
            db.add(
                RehabilitationFacilityLocation(
                    id=str(uuid4()),
                    facility_id=facility_id,
                    location_type=row.get("location_type") or "extracted_address",
                    location_name=row.get("location_name") or row.get("full_address") or "Unknown",
                    country_code=row.get("country_code") or "AT",
                    country_name=row.get("country_name") or "Austria",
                    region=row.get("region"),
                    district=row.get("district"),
                    city=row.get("city"),
                    area=row.get("area"),
                    full_address=row.get("full_address"),
                    postal_code=row.get("postal_code"),
                    latitude=row.get("latitude"),
                    longitude=row.get("longitude"),
                    is_primary=_as_bool(row.get("is_primary"), False),
                    verification_status=row.get("verification_status")
                    or "verified_from_staging",
                    confidence_score=float(row.get("confidence_score") or 0.5),
                    is_mock=_as_bool(row.get("is_mock"), False),
                )
            )

        for row in aliases_src:
            facility_id = facility_id_map.get(row["facility_id"])
            if not facility_id:
                continue
            db.add(
                RehabilitationFacilityAlias(
                    id=str(uuid4()),
                    facility_id=facility_id,
                    name=row["name"],
                    language_code=row.get("language_code"),
                    alias_type=row.get("alias_type") or "alternate",
                    is_primary=_as_bool(row.get("is_primary"), False),
                    is_mock=_as_bool(row.get("is_mock"), False),
                )
            )

        await db.commit()

        summary = {
            "removed_prior_missions": removed,
            "organization_id": org.id,
            "mission_id": mission_id,
            "run_id": run_id,
            "execution_id": execution_id,
            "facilities": len(facilities_src),
            "contacts": len(contacts_src),
            "locations": len(locations_src),
            "aliases": len(aliases_src),
            "ui_path": f"/scraping/{mission_id}/executions/{execution_id}",
        }
        print(json.dumps(summary, indent=2))
        return summary


def main() -> None:
    try:
        asyncio.run(seed_austria_demo())
    except Exception as exc:  # noqa: BLE001 — CLI entrypoint
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
