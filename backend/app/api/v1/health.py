from fastapi import APIRouter

from app.core.config import get_settings
from app.services.document_processing import upload_max_bytes

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "multiai-api"}


@router.get("/client-config")
async def client_config():
    """Upload limits the composer can apply. The feature flag defaults off."""
    settings = get_settings()
    return {
        "large_documents_enabled": settings.large_documents_enabled,
        "attachment_max_bytes": upload_max_bytes(),
        "library_max_bytes": upload_max_bytes(library=True),
    }
