"""SQLAlchemy ORM models — multi-tenant enterprise schema (SQLite + PostgreSQL)."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


def UuidFK(table: str, *, nullable: bool = False) -> Mapped[str]:
    return mapped_column(String(36), ForeignKey(f"{table}.id"), nullable=nullable)


class OrgRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class TurnStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class ModelAnswerStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Strategy(str, enum.Enum):
    RECONCILE = "Reconcile"
    SYNTHESIZE = "Synthesize"
    RANK = "Rank"
    PICK_BEST = "Pick Best"
    DEBATE = "Debate"


class UsageKind(str, enum.Enum):
    ANSWER = "answer"
    VERDICT = "verdict"
    INSURANCE = "insurance"
    LESSON = "lesson"
    BRAIN = "brain"


class LessonStatus(str, enum.Enum):
    BUILDING = "building"
    COMPLETED = "completed"
    FAILED = "failed"


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    memberships: Mapped[list["OrgMembership"]] = relationship(back_populates="user")
    preferences: Mapped["UserPreferences | None"] = relationship(back_populates="user")
    brain: Mapped["UserBrain | None"] = relationship(back_populates="user")


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(50), default="pro", nullable=False)
    monthly_budget_cents: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)

    memberships: Mapped[list["OrgMembership"]] = relationship(back_populates="organization")
    projects: Mapped[list["Project"]] = relationship(back_populates="organization")
    chats: Mapped[list["Chat"]] = relationship(back_populates="organization")
    model_sets: Mapped[list["ModelSet"]] = relationship(back_populates="organization")
    templates: Mapped[list["Template"]] = relationship(back_populates="organization")
    org_models: Mapped[list["OrgModel"]] = relationship(back_populates="organization")


class OrgModel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """User-added OpenRouter models available to an organization."""

    __tablename__ = "org_models"
    __table_args__ = (UniqueConstraint("org_id", "model_id", name="uq_org_model"),)

    org_id: Mapped[str] = UuidFK("organizations")
    model_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    openrouter_slug: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor: Mapped[str] = mapped_column(String(128), nullable=False)
    blurb: Mapped[str] = mapped_column(Text, default="", nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="org_models")


class OrgMembership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "org_memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_org_user"),)

    org_id: Mapped[str] = UuidFK("organizations")
    user_id: Mapped[str] = UuidFK("users")
    role: Mapped[OrgRole] = mapped_column(Enum(OrgRole), default=OrgRole.MEMBER, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")


class UserPreferences(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True)
    default_model_set_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    default_verdict_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    theme: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    response_style: Mapped[str] = mapped_column(String(16), default="Balanced", nullable=False)

    user: Mapped["User"] = relationship(back_populates="preferences")


class UserBrain(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Persistent memory of how a user thinks — learned from disagreements and fed into chat."""

    __tablename__ = "user_brains"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_brain_user"),)

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    org_id: Mapped[str] = UuidFK("organizations")
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    thinking_style: Mapped[str] = mapped_column(Text, default="", nullable=False)
    likes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    dislikes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    memories: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    lesson_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped["User"] = relationship(back_populates="brain")


class Project(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "projects"

    org_id: Mapped[str] = UuidFK("organizations")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="projects")
    chats: Mapped[list["Chat"]] = relationship(back_populates="project")


class Chat(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "chats"

    org_id: Mapped[str] = UuidFK("organizations")
    project_id: Mapped[str | None] = UuidFK("projects", nullable=True)
    created_by: Mapped[str] = UuidFK("users")
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="New chat")

    organization: Mapped["Organization"] = relationship(back_populates="chats")
    project: Mapped["Project | None"] = relationship(back_populates="chats")
    turns: Mapped[list["Turn"]] = relationship(back_populates="chat", order_by="Turn.created_at")
    share_links: Mapped[list["ShareLink"]] = relationship(back_populates="chat")


class ModelSet(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "model_sets"

    org_id: Mapped[str | None] = UuidFK("organizations", nullable=True)
    slug: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    models: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    verdict_model: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[Strategy] = mapped_column(Enum(Strategy), nullable=False)
    best_for: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    template_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organization: Mapped["Organization | None"] = relationship(back_populates="model_sets")


class Template(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "templates"

    org_id: Mapped[str | None] = UuidFK("organizations", nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organization: Mapped["Organization | None"] = relationship(back_populates="templates")


class Turn(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "turns"

    chat_id: Mapped[str] = UuidFK("chats")
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    model_set_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[Strategy] = mapped_column(Enum(Strategy), nullable=False)
    verdict_model: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TurnStatus] = mapped_column(
        Enum(TurnStatus), default=TurnStatus.PENDING, nullable=False
    )
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_insurance_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    chat: Mapped["Chat"] = relationship(back_populates="turns")
    model_answers: Mapped[list["ModelAnswer"]] = relationship(back_populates="turn")
    verdict: Mapped["Verdict | None"] = relationship(back_populates="turn")
    decision_insurance: Mapped["DecisionInsurance | None"] = relationship(back_populates="turn")
    cost_records: Mapped[list["CostRecord"]] = relationship(back_populates="turn")
    lesson: Mapped["VerdictLesson | None"] = relationship(back_populates="turn")


class ModelAnswer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "model_answers"

    turn_id: Mapped[str] = UuidFK("turns")
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[ModelAnswerStatus] = mapped_column(
        Enum(ModelAnswerStatus), default=ModelAnswerStatus.PENDING, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    turn: Mapped["Turn"] = relationship(back_populates="model_answers")


class Verdict(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "verdicts"

    turn_id: Mapped[str] = mapped_column(String(36), ForeignKey("turns.id"), unique=True)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[Strategy] = mapped_column(Enum(Strategy), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    turn: Mapped["Turn"] = relationship(back_populates="verdict")


class VerdictLesson(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Structured lesson built when a user disagrees with the AI verdict."""

    __tablename__ = "verdict_lessons"
    __table_args__ = (UniqueConstraint("turn_id", name="uq_verdict_lesson_turn"),)

    turn_id: Mapped[str] = mapped_column(String(36), ForeignKey("turns.id"), nullable=False)
    chat_id: Mapped[str] = UuidFK("chats")
    org_id: Mapped[str] = UuidFK("organizations")
    user_id: Mapped[str] = UuidFK("users")
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    disagreement_reason: Mapped[str] = mapped_column(Text, nullable=False)
    user_position: Mapped[str] = mapped_column(Text, nullable=False)
    verdict_model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict_model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    verdict_text: Mapped[str] = mapped_column(Text, nullable=False)
    verdict_reason: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[Strategy] = mapped_column(Enum(Strategy), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    comparison: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[LessonStatus] = mapped_column(
        Enum(LessonStatus), default=LessonStatus.BUILDING, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    turn: Mapped["Turn"] = relationship(back_populates="lesson")


class DecisionInsurance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "decision_insurance"

    turn_id: Mapped[str] = mapped_column(String(36), ForeignKey("turns.id"), unique=True)
    best_case: Mapped[str] = mapped_column(Text, nullable=False)
    worst_case: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    potential_loss: Mapped[str] = mapped_column(Text, nullable=False)
    mitigation_plan: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    turn: Mapped["Turn"] = relationship(back_populates="decision_insurance")


class CostRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "cost_records"

    org_id: Mapped[str] = UuidFK("organizations")
    chat_id: Mapped[str] = UuidFK("chats")
    project_id: Mapped[str | None] = UuidFK("projects", nullable=True)
    turn_id: Mapped[str] = UuidFK("turns")
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[UsageKind] = mapped_column(Enum(UsageKind), nullable=False)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)

    turn: Mapped["Turn"] = relationship(back_populates="cost_records")


class ShareLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "share_links"

    chat_id: Mapped[str] = UuidFK("chats")
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_by: Mapped[str] = UuidFK("users")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chat: Mapped["Chat"] = relationship(back_populates="share_links")
