"""One-shot AI planner that returns official registry/directory URLs for a country."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.db.models import UsageKind
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry
from app.schemas.api import OfficialSourceSeed, OfficialSourceSeedPlan
from app.services.scraping.cost_tracking import record_scraping_llm
from app.services.scraping.url_canonicalization import UrlRejected, canonicalize_discovery_url

MAX_OFFICIAL_SEEDS = 16
ALLOWED_TRUST_TIERS = {"official", "high", "medium"}


class OfficialSourceSeedPlanner:
    async def plan(
        self,
        *,
        country_code: str,
        country_name: str,
        mission_goal: str,
        requested_fields: list[str],
        registry_hints: list[str],
        source_strategy: list[str],
        max_sources: int = MAX_OFFICIAL_SEEDS,
        db: AsyncSession | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        mission_id: str | None = None,
        execution_id: str | None = None,
    ) -> list[OfficialSourceSeed]:
        model = get_model("gpt-4.1")
        provider = get_provider_registry().get_provider(model.provider)
        capped = min(max(int(max_sources or MAX_OFFICIAL_SEEDS), 1), MAX_OFFICIAL_SEEDS)
        prompt = get_prompt_engine().render(
            "scraping/official_source_seed_planner.j2",
            max_sources=capped,
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            mission_goal=(mission_goal or "Find official facility directories.")[:2000],
            requested_fields=requested_fields[:50],
            registry_hints=registry_hints[:20],
            source_strategy=source_strategy[:20],
        )
        idem = f"official-seed:{execution_id or mission_id or 'unknown'}"
        try:
            response = await provider.complete(
                system="You return strict JSON listing official registry and directory URLs.",
                user=prompt,
                model=model.provider_model,
                max_tokens=2000,
            )
        except Exception:
            await record_scraping_llm(
                db,
                org_id=org_id,
                user_id=user_id,
                mission_id=mission_id,
                execution_id=execution_id,
                model_id="gpt-4.1",
                kind=UsageKind.SCRAPING,
                operation="official_source_seed",
                idempotency_key=f"{idem}:failed",
                failed=True,
                error_code="official_source_seed_failed",
            )
            raise
        await record_scraping_llm(
            db,
            org_id=org_id,
            user_id=user_id,
            mission_id=mission_id,
            execution_id=execution_id,
            model_id="gpt-4.1",
            kind=UsageKind.SCRAPING,
            operation="official_source_seed",
            idempotency_key=idem,
            response=response,
        )
        try:
            raw = LLMProvider.parse_json_response(response.text)
            plan = OfficialSourceSeedPlan.model_validate(
                _normalize_seed_payload(raw, max_sources=capped)
            )
        except (PydanticValidationError, Exception) as exc:
            raise ValidationError("Official source seed planning failed.") from exc
        return _dedupe_valid_seeds(plan.sources, max_sources=capped)


def _normalize_seed_payload(raw: Any, *, max_sources: int) -> dict[str, Any]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("sources") or raw.get("urls") or raw.get("seeds") or []
    else:
        items = []
    if not isinstance(items, list):
        items = []
    normalized: list[dict[str, Any]] = []
    for item in items[: max_sources * 2]:
        if isinstance(item, str):
            normalized.append(
                {
                    "url": item,
                    "title": "",
                    "purpose": "Official or high-trust directory seed",
                    "trust_tier": "official",
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        trust = str(item.get("trust_tier") or "official").strip().casefold()
        if trust not in ALLOWED_TRUST_TIERS:
            trust = "official"
        normalized.append(
            {
                "url": str(item.get("url") or item.get("href") or "").strip(),
                "title": str(item.get("title") or "")[:300],
                "purpose": str(item.get("purpose") or item.get("reason") or "")[:300],
                "trust_tier": trust,
            }
        )
    return {"sources": normalized[:max_sources]}


def _dedupe_valid_seeds(
    seeds: list[OfficialSourceSeed],
    *,
    max_sources: int,
) -> list[OfficialSourceSeed]:
    result: list[OfficialSourceSeed] = []
    seen: set[str] = set()
    for seed in seeds:
        try:
            canonical = canonicalize_discovery_url(seed.url)
        except UrlRejected:
            continue
        key = canonical.canonical_url.casefold()
        if key in seen:
            continue
        seen.add(key)
        trust = seed.trust_tier.casefold() if seed.trust_tier else "official"
        if trust not in ALLOWED_TRUST_TIERS:
            trust = "official"
        result.append(
            OfficialSourceSeed(
                url=canonical.canonical_url,
                title=(seed.title or canonical.domain)[:300],
                purpose=(seed.purpose or "Official registry or directory seed")[:300],
                trust_tier=trust,
            )
        )
        if len(result) >= max_sources:
            break
    return result


official_source_seed_planner = OfficialSourceSeedPlanner()
