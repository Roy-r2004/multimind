"""Organization model catalog — built-ins + OpenRouter additions."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.db.models import OrgModel
from app.llm.catalog import (
    MODEL_CATALOG,
    enrich_entry,
    entry_from_slug,
    is_builtin_model_id,
    slug_to_model_id,
)
from app.llm.pricing import get_pricing_service, vendor_from_slug
from app.schemas.api import ModelAddRequest, ModelResponse, ModelSearchResult, ModelPricingResponse


def _to_response(model_id: str, *, name: str, vendor: str, color: str, blurb: str, is_custom: bool) -> ModelResponse:
    pricing_svc = get_pricing_service()
    p = pricing_svc.get_pricing(model_id)
    return ModelResponse(
        id=model_id,
        name=name,
        vendor=vendor,
        color=color,
        blurb=blurb,
        is_custom=is_custom,
        openrouter_slug=p.openrouter_slug or None,
        pricing=ModelPricingResponse(
            input_per_1k=p.input_per_1k,
            output_per_1k=p.output_per_1k,
            source=p.source,
            openrouter_slug=p.openrouter_slug,
        ),
    )


class ModelCatalogService:
    async def list_for_org(self, db: AsyncSession, auth: AuthContext) -> list[ModelResponse]:
        await get_pricing_service().ensure_loaded()

        items: list[ModelResponse] = []
        for entry in MODEL_CATALOG.values():
            live = enrich_entry(entry)
            items.append(
                _to_response(
                    live.id,
                    name=live.name,
                    vendor=live.vendor,
                    color=live.color,
                    blurb=live.blurb,
                    is_custom=False,
                )
            )

        result = await db.execute(select(OrgModel).where(OrgModel.org_id == auth.org_id))
        for row in result.scalars().all():
            if row.model_id in MODEL_CATALOG:
                continue
            entry = entry_from_slug(row.model_id, row.openrouter_slug, name=row.name, vendor=row.vendor, blurb=row.blurb)
            items.append(
                _to_response(
                    entry.id,
                    name=entry.name,
                    vendor=entry.vendor,
                    color=entry.color,
                    blurb=entry.blurb,
                    is_custom=True,
                )
            )
        return items

    async def search(self, query: str, *, limit: int = 20) -> list[ModelSearchResult]:
        await get_pricing_service().ensure_loaded()
        svc = get_pricing_service()
        return [ModelSearchResult(**item) for item in svc.search_models(query, limit=limit)]

    async def add(self, db: AsyncSession, auth: AuthContext, body: ModelAddRequest) -> ModelResponse:
        slug = body.openrouter_slug.strip()
        if not slug or "/" not in slug:
            raise ValidationError("Invalid OpenRouter model slug")

        await get_pricing_service().ensure_loaded()

        for entry in MODEL_CATALOG.values():
            if entry.provider_model == slug:
                live = enrich_entry(entry)
                return _to_response(
                    live.id,
                    name=live.name,
                    vendor=live.vendor,
                    color=live.color,
                    blurb=live.blurb,
                    is_custom=False,
                )

        meta = get_pricing_service().get_slug_metadata(slug)
        if meta is None:
            raise NotFoundError("OpenRouter model", slug)

        model_id = slug_to_model_id(slug)
        if is_builtin_model_id(model_id):
            raise ConflictError("This model is already in the default catalog")

        existing = await db.execute(
            select(OrgModel).where(OrgModel.org_id == auth.org_id, OrgModel.model_id == model_id)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("Model already added")

        name = meta.get("name") or slug.split("/")[-1]
        vendor = vendor_from_slug(slug)
        blurb = (meta.get("description") or "")[:500]

        row = OrgModel(
            org_id=auth.org_id,
            model_id=model_id,
            openrouter_slug=slug,
            name=name,
            vendor=vendor,
            blurb=blurb,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

        entry = entry_from_slug(model_id, slug, name=name, vendor=vendor, blurb=blurb)
        return _to_response(
            entry.id,
            name=entry.name,
            vendor=entry.vendor,
            color=entry.color,
            blurb=entry.blurb,
            is_custom=True,
        )

    async def remove(self, db: AsyncSession, auth: AuthContext, model_id: str) -> None:
        if is_builtin_model_id(model_id):
            raise ValidationError("Cannot remove built-in models")

        result = await db.execute(
            select(OrgModel).where(OrgModel.org_id == auth.org_id, OrgModel.model_id == model_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("Model", model_id)
        await db.delete(row)
        await db.commit()


model_catalog_service = ModelCatalogService()
