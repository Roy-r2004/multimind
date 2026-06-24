from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.models import User
from app.db.session import get_db
from app.schemas.api import (
    SessionResponse,
    SignInRequest,
    SignUpRequest,
    TokenResponse,
)
from app.services.auth_service import auth_service

router = APIRouter()


@router.post("/signup", response_model=TokenResponse)
async def sign_up(data: SignUpRequest, db: AsyncSession = Depends(get_db)):
    session = await auth_service.sign_up(db, data)
    from sqlalchemy import select
    from app.db.models import User

    result = await db.execute(select(User).where(User.id == session.user.id))
    user = result.scalar_one()
    token = auth_service.create_token(user, session.organization.id)
    return TokenResponse(access_token=token)


@router.post("/signin", response_model=TokenResponse)
async def sign_in(data: SignInRequest, db: AsyncSession = Depends(get_db)):
    session = await auth_service.sign_in(db, data)
    from sqlalchemy import select
    from app.db.models import User

    result = await db.execute(select(User).where(User.id == session.user.id))
    user = result.scalar_one()
    token = auth_service.create_token(user, session.organization.id)
    return TokenResponse(access_token=token)


@router.get("/session", response_model=SessionResponse)
async def get_session(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await auth_service.get_session(db, auth.user.id)
