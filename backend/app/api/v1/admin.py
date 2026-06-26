from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, require_org_admin
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.security import hash_password
from app.db.models import (
    Chat,
    CostRecord,
    ModelSet,
    OrgMembership,
    OrgRole,
    Organization,
    Project,
    Template,
    Turn,
    User,
)
from app.db.session import get_db
from app.schemas.api import (
    AdminCreateMemberRequest,
    AdminMemberActionResponse,
    AdminMemberResponse,
    AdminOverviewResponse,
    AdminUpdateMemberRequest,
    AdminUsageResponse,
)
from app.services.audit_service import audit_service
from app.services.domain_service import cost_service

router = APIRouter()
MANAGEABLE_ROLES = {OrgRole.ADMIN, OrgRole.MEMBER, OrgRole.VIEWER}


async def _count(db: AsyncSession, statement) -> int:
    result = await db.execute(statement)
    return int(result.scalar_one() or 0)


def _member_response(user: User, membership: OrgMembership) -> AdminMemberResponse:
    return AdminMemberResponse(
        id=membership.id,
        membership_id=membership.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=membership.role.value,
        is_active=user.is_active,
        created_at=membership.created_at,
        joined_at=membership.created_at,
    )


def _parse_manageable_role(role: str) -> OrgRole:
    try:
        parsed = OrgRole(role)
    except ValueError as exc:
        raise AppError("Invalid role") from exc
    if parsed not in MANAGEABLE_ROLES:
        raise AppError("Role must be admin, member, or viewer")
    return parsed


async def _get_membership_with_user(
    db: AsyncSession,
    auth: AuthContext,
    membership_id: str,
) -> tuple[User, OrgMembership]:
    result = await db.execute(
        select(User, OrgMembership)
        .join(OrgMembership, OrgMembership.user_id == User.id)
        .where(OrgMembership.id == membership_id, OrgMembership.org_id == auth.org_id)
    )
    row = result.first()
    if row is None:
        raise NotFoundError("Membership", membership_id)
    user, membership = row
    return user, membership


async def _active_owner_admin_count(
    db: AsyncSession,
    auth: AuthContext,
    *,
    exclude_membership_id: str | None = None,
) -> int:
    statement = (
        select(func.count())
        .select_from(OrgMembership)
        .join(User, User.id == OrgMembership.user_id)
        .where(
            OrgMembership.org_id == auth.org_id,
            OrgMembership.role.in_([OrgRole.OWNER, OrgRole.ADMIN]),
            User.is_active.is_(True),
        )
    )
    if exclude_membership_id:
        statement = statement.where(OrgMembership.id != exclude_membership_id)
    return await _count(db, statement)


async def _ensure_self_change_keeps_admin_access(
    db: AsyncSession,
    auth: AuthContext,
    user: User,
    membership: OrgMembership,
) -> None:
    if user.id != auth.user.id:
        return
    remaining = await _active_owner_admin_count(db, auth, exclude_membership_id=membership.id)
    if remaining < 1:
        raise AppError("Cannot remove your own last owner/admin access")


@router.get("/overview", response_model=AdminOverviewResponse)
async def admin_overview(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    org = await db.get(Organization, auth.org_id)

    total_members = await _count(
        db,
        select(func.count()).select_from(OrgMembership).where(OrgMembership.org_id == auth.org_id),
    )
    total_projects = await _count(
        db,
        select(func.count()).select_from(Project).where(Project.org_id == auth.org_id),
    )
    total_chats = await _count(
        db,
        select(func.count()).select_from(Chat).where(Chat.org_id == auth.org_id),
    )
    total_model_sets = await _count(
        db,
        select(func.count())
        .select_from(ModelSet)
        .where((ModelSet.org_id == auth.org_id) | (ModelSet.is_system.is_(True))),
    )
    total_templates = await _count(
        db,
        select(func.count())
        .select_from(Template)
        .where((Template.org_id == auth.org_id) | (Template.is_system.is_(True))),
    )

    return AdminOverviewResponse(
        organization_id=auth.org_id,
        organization_name=org.name if org else "",
        organization_slug=org.slug if org else "",
        plan=org.plan if org else "",
        user_role=auth.role.value,
        total_members=total_members,
        total_projects=total_projects,
        total_chats=total_chats,
        total_model_sets=total_model_sets,
        total_templates=total_templates,
        monthly_budget_usd=(org.monthly_budget_cents / 100.0) if org else 0.0,
    )


@router.get("/members", response_model=list[AdminMemberResponse])
async def admin_members(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User, OrgMembership)
        .join(OrgMembership, OrgMembership.user_id == User.id)
        .where(OrgMembership.org_id == auth.org_id)
        .order_by(OrgMembership.created_at.asc())
    )

    return [
        _member_response(user, membership)
        for user, membership in result.all()
    ]


@router.post("/members", response_model=AdminMemberResponse, status_code=201)
async def admin_create_member(
    data: AdminCreateMemberRequest,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    role = _parse_manageable_role(data.role)
    email = data.email.lower()

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        full_name = data.full_name.strip()
        if not full_name:
            raise AppError("Name is required")
        user = User(
            email=email,
            full_name=full_name,
            hashed_password=hash_password(data.temporary_password),
            is_active=True,
        )
        db.add(user)
        await db.flush()

    existing = await db.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == auth.org_id,
            OrgMembership.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("User is already a member of this organization")

    membership = OrgMembership(org_id=auth.org_id, user_id=user.id, role=role)
    db.add(membership)
    user.is_active = True
    await db.flush()

    actor = await db.get(User, auth.user.id)
    if actor:
        await audit_service.record_admin_member(
            db,
            org_id=auth.org_id,
            actor=actor,
            action="admin.member.create",
            target_user=user,
            summary=f"{actor.email} added member {user.email} as {role.value}",
            metadata={"role": role.value},
        )

    return _member_response(user, membership)


@router.patch("/members/{membership_id}", response_model=AdminMemberResponse)
async def admin_update_member(
    membership_id: str,
    data: AdminUpdateMemberRequest,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    user, membership = await _get_membership_with_user(db, auth, membership_id)
    if membership.role == OrgRole.OWNER:
        raise AppError("Organization owners cannot be modified here")

    next_role = membership.role
    if data.role is not None:
        next_role = _parse_manageable_role(data.role)

    next_is_active = user.is_active if data.is_active is None else data.is_active
    loses_admin_access = membership.role == OrgRole.ADMIN and (
        next_role != OrgRole.ADMIN or not next_is_active
    )
    if loses_admin_access:
        await _ensure_self_change_keeps_admin_access(db, auth, user, membership)

    membership.role = next_role
    user.is_active = next_is_active
    await db.flush()

    actor = await db.get(User, auth.user.id)
    if actor:
        await audit_service.record_admin_member(
            db,
            org_id=auth.org_id,
            actor=actor,
            action="admin.member.update",
            target_user=user,
            summary=f"{actor.email} updated member {user.email}",
            metadata={"role": next_role.value, "is_active": next_is_active},
        )

    return _member_response(user, membership)


@router.delete("/members/{membership_id}", response_model=AdminMemberActionResponse)
async def admin_delete_member(
    membership_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    user, membership = await _get_membership_with_user(db, auth, membership_id)
    if membership.role == OrgRole.OWNER:
        raise AppError("Organization owners cannot be removed here")
    if membership.role == OrgRole.ADMIN:
        await _ensure_self_change_keeps_admin_access(db, auth, user, membership)

    actor = await db.get(User, auth.user.id)
    if actor:
        await audit_service.record_admin_member(
            db,
            org_id=auth.org_id,
            actor=actor,
            action="admin.member.remove",
            target_user=user,
            summary=f"{actor.email} removed member {user.email}",
        )

    await db.delete(membership)
    await db.flush()
    return AdminMemberActionResponse(message="Member removed")


@router.get("/usage", response_model=AdminUsageResponse)
async def admin_usage(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    summary = await cost_service.summary(db, auth)
    total_turns = await _count(
        db,
        select(func.count())
        .select_from(Turn)
        .join(Chat, Chat.id == Turn.chat_id)
        .where(Chat.org_id == auth.org_id),
    )
    total_cost_records = await _count(
        db,
        select(func.count()).select_from(CostRecord).where(CostRecord.org_id == auth.org_id),
    )

    return AdminUsageResponse(
        **summary.model_dump(),
        total_turns=total_turns,
        total_cost_records=total_cost_records,
    )
