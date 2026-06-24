from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.llm.pricing import get_pricing_service
from app.schemas.api import CostSummaryResponse, PricingCatalogResponse, PricingCatalogItem
from app.services.domain_service import cost_service

router = APIRouter()


@router.get("/summary", response_model=CostSummaryResponse)
async def cost_summary(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await cost_service.summary(db, auth)


@router.get("/pricing", response_model=PricingCatalogResponse)
async def cost_pricing(_auth: AuthContext = Depends(get_auth_context)):
    service = get_pricing_service()
    await service.ensure_loaded()
    return PricingCatalogResponse(
        updated_at=service.last_refresh,
        models=[PricingCatalogItem(**item) for item in service.catalog_snapshot()],
    )
