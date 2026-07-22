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
from app.services.audit_service import audit_service
from app.db.session import AsyncSessionLocal, engine

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

    # Never block process boot on LLM/network — health must answer during cold starts.
    try:
        get_provider_registry().validate_configured()
    except AppError as exc:
        logger.warning("llm_keys_missing", message=exc.message)

    if settings.environment == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _warm_pricing() -> None:
        try:
            await get_pricing_service().refresh()
        except Exception as exc:
            logger.warning("openrouter_pricing_startup_failed", error=str(exc))

    import asyncio

    asyncio.create_task(_warm_pricing())
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

    cors_origins = list(dict.fromkeys(settings.cors_origins))
    if settings.is_production:
        cors_origins.extend(
            [
                "https://multiai-web.onrender.com",
                settings.public_app_url.rstrip("/"),
            ]
        )
        cors_origins = list(dict.fromkeys(origin for origin in cors_origins if origin))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=r"https://.*\.onrender\.com" if settings.is_production else None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def audit_trail_middleware(request: Request, call_next):
        response = await call_next(request)
        try:
            async with AsyncSessionLocal() as db:
                await audit_service.record_http(db, request, response.status_code)
                await db.commit()
        except Exception:
            logger.exception("audit_middleware_failed")
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        status_map = {
            "NOT_FOUND": 404,
            "UNAUTHORIZED": 401,
            "FORBIDDEN": 403,
            "VALIDATION_ERROR": 422,
            "CONFLICT": 409,
            "INTERNAL_ERROR": 500,
            "LLM_NOT_CONFIGURED": 503,
            "TRANSCRIPTION_DISABLED": 503,
            "TRANSCRIPTION_MODEL_UNAVAILABLE": 503,
            "TRANSCRIPTION_BUSY": 429,
            "TRANSCRIPTION_TIMEOUT": 504,
            "UNSUPPORTED_AUDIO_TYPE": 415,
            "AUDIO_TOO_LARGE": 413,
            "INVALID_AUDIO": 422,
            "AUDIO_TOO_LONG": 422,
            "SILENT_AUDIO": 422,
        }
        headers = {"Retry-After": "5"} if exc.code == "TRANSCRIPTION_BUSY" else None
        return ORJSONResponse(
            status_code=status_map.get(exc.code, 400),
            content={"error": exc.code, "message": exc.message, "details": exc.details},
            headers=headers,
        )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
