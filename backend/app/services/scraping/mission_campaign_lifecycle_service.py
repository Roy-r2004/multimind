"""Mission-direct lifecycle facade for Phase 2A campaigns."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.schemas.api import ScrapingExecutionDetail, ScrapingExecutionSummary
from app.services.scraping.execution_service import execution_service


class MissionCampaignLifecycleService:
    async def start(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> ScrapingExecutionSummary:
        return await execution_service.start_mission_campaign(db, auth, mission_id)

    async def list(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> list[ScrapingExecutionSummary]:
        return await execution_service.list_mission_campaigns(db, auth, mission_id)

    async def status(
        self, db: AsyncSession, auth: AuthContext, mission_id: str, execution_id: str
    ) -> ScrapingExecutionDetail:
        return await execution_service.get_mission_campaign_detail(db, auth, mission_id, execution_id)

    async def pause(
        self, db: AsyncSession, auth: AuthContext, mission_id: str, execution_id: str
    ) -> ScrapingExecutionSummary:
        return await execution_service.pause_mission_campaign(db, auth, mission_id, execution_id)

    async def resume(
        self, db: AsyncSession, auth: AuthContext, mission_id: str, execution_id: str
    ) -> ScrapingExecutionSummary:
        return await execution_service.resume_mission_campaign(db, auth, mission_id, execution_id)

    async def cancel(
        self, db: AsyncSession, auth: AuthContext, mission_id: str, execution_id: str
    ) -> ScrapingExecutionSummary:
        await execution_service._mission_campaign_row(db, auth, mission_id, execution_id)
        return await execution_service.cancel_execution(db, auth, execution_id)


mission_campaign_lifecycle_service = MissionCampaignLifecycleService()
