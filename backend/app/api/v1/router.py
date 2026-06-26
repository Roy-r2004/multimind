"""API v1 route modules."""

from fastapi import APIRouter

from app.api.v1 import admin, admin_insights, auth, brain, chats, costs, health, lessons, model_sets, models, projects, share, templates

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(admin_insights.router, prefix="/admin", tags=["admin"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(chats.router, prefix="/chats", tags=["chats"])
api_router.include_router(share.router, prefix="/share", tags=["share"])
api_router.include_router(model_sets.router, prefix="/model-sets", tags=["model-sets"])
api_router.include_router(templates.router, prefix="/templates", tags=["templates"])
api_router.include_router(costs.router, prefix="/costs", tags=["costs"])
api_router.include_router(lessons.router, prefix="/lessons", tags=["lessons"])
api_router.include_router(brain.router, prefix="/brain", tags=["brain"])
