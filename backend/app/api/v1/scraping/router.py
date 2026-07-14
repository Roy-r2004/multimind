"""Scraping Council API router."""

from fastapi import APIRouter

from app.api.v1.scraping import blueprints, missions, runs

router = APIRouter()
router.include_router(missions.router, prefix="/missions", tags=["scraping"])
router.include_router(blueprints.router, prefix="/blueprints", tags=["scraping"])
router.include_router(runs.router, prefix="/runs", tags=["scraping"])
