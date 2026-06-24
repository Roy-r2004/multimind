"""MultiAI Enterprise API — FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger, setup_logging
from app.db.base import Base
from app.db.session import engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    logger.info(
        "application_starting",
        app=settings.app_name,
        environment=settings.environment,
    )
    from app.core.exceptions import AppError
    from app.llm.pricing import get_pricing_service
    from app.llm.providers import get_provider_registry

    try:
        get_provider_registry().validate_configured()
    except AppError as exc:
        if settings.is_production:
            raise
        logger.warning("llm_keys_missing", message=exc.message)
    else:
        try:
            await get_pricing_service().refresh()
        except Exception as exc:
            logger.warning("openrouter_pricing_startup_failed", error=str(exc))
    if settings.environment == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield
    logger.info("application_shutdown")
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        status_map = {
            "NOT_FOUND": 404,
            "UNAUTHORIZED": 401,
            "FORBIDDEN": 403,
            "VALIDATION_ERROR": 422,
            "CONFLICT": 409,
            "LLM_NOT_CONFIGURED": 503,
        }
        return ORJSONResponse(
            status_code=status_map.get(exc.code, 400),
            content={"error": exc.code, "message": exc.message, "details": exc.details},
        )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
