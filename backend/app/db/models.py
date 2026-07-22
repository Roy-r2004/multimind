"""SQLAlchemy ORM models — multi-tenant enterprise schema (SQLite + PostgreSQL)."""

import enum
import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
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
    DISCUSSING = "discussing"
    BUILDING = "building"
    COMPLETED = "completed"
    FAILED = "failed"


class ScrapingMissionStatus(str, enum.Enum):
    DRAFT = "draft"
    BLUEPRINT_GENERATING = "blueprint_generating"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScrapingBlueprintStatus(str, enum.Enum):
    GENERATING = "generating"
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class ScrapingRunStatus(str, enum.Enum):
    PLANNING = "planning"
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScrapingRunAgentStatus(str, enum.Enum):
    PLANNED = "planned"
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScrapingExecutionStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScrapingExecutionAgentStatus(str, enum.Enum):
    WAITING = "waiting"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScrapingCoverageStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COVERED = "covered"
    COVERED_NO_RESULTS = "covered_no_results"
    PARTIALLY_COVERED = "partially_covered"
    BLOCKED = "blocked"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScrapingTaskStatus(str, enum.Enum):
    QUEUED = "queued"
    BLOCKED = "blocked"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceDiscoveryQueryStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SourceCandidateStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


class SourceRetrievalAttemptStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    BLOCKED_BY_ROBOTS = "blocked_by_robots"
    UNSAFE_URL = "unsafe_url"
    DNS_RESOLUTION_FAILED = "dns_resolution_failed"
    PRIVATE_OR_RESERVED_ADDRESS = "private_or_reserved_address"
    REDIRECT_LIMIT_EXCEEDED = "redirect_limit_exceeded"
    UNSAFE_REDIRECT = "unsafe_redirect"
    TIMEOUT = "timeout"
    CONNECTION_FAILED = "connection_failed"
    PROVIDER_HTTP_ERROR = "provider_http_error"
    RESPONSE_TOO_LARGE = "response_too_large"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    MALFORMED_CONTENT = "malformed_content"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SourceRetrievalRobotsStatus(str, enum.Enum):
    ALLOWED = "allowed"
    NO_RULES = "no_rules"
    DISALLOWED = "disallowed"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


class SourceDocumentTextPreparationStatus(str, enum.Enum):
    PREPARED = "prepared"
    FAILED = "failed"


class FacilityExtractionAttemptStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FacilityCandidateStagingStatus(str, enum.Enum):
    EXTRACTED = "extracted"
    EVIDENCE_REJECTED = "evidence_rejected"
    REVIEW_REQUIRED = "review_required"
    SUPERSEDED = "superseded"


class FacilityCandidateEvidenceVerificationStatus(str, enum.Enum):
    VERIFIED = "verified"
    REJECTED_QUOTE_NOT_FOUND = "rejected_quote_not_found"
    REJECTED_QUOTE_TOO_LONG = "rejected_quote_too_long"


class FacilityCandidatePublicationStatus(str, enum.Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    SKIPPED = "skipped"
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
    scraping_missions: Mapped[list["ScrapingMission"]] = relationship(back_populates="organization")


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
    scraping_missions: Mapped[list["ScrapingMission"]] = relationship(back_populates="project")


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


class ScrapingMission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_missions"
    __table_args__ = (
        Index("ix_scraping_missions_org_id", "org_id"),
        Index("ix_scraping_missions_created_by", "created_by"),
        Index("ix_scraping_missions_status", "status"),
        Index("ix_scraping_missions_updated_at", "updated_at"),
    )

    org_id: Mapped[str] = UuidFK("organizations")
    created_by: Mapped[str] = UuidFK("users")
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    model_set_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    original_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    country_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[ScrapingMissionStatus] = mapped_column(
        Enum(
            ScrapingMissionStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=ScrapingMissionStatus.DRAFT,
        nullable=False,
    )
    active_blueprint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="scraping_missions")
    creator: Mapped["User"] = relationship()
    project: Mapped["Project | None"] = relationship(back_populates="scraping_missions")
    blueprints: Mapped[list["ScrapingBlueprint"]] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        order_by="ScrapingBlueprint.version",
        foreign_keys="ScrapingBlueprint.mission_id",
    )
    active_blueprint: Mapped["ScrapingBlueprint | None"] = relationship(
        "ScrapingBlueprint",
        primaryjoin="ScrapingMission.active_blueprint_id == foreign(ScrapingBlueprint.id)",
        viewonly=True,
    )
    runs: Mapped[list["ScrapingRun"]] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        order_by="ScrapingRun.created_at",
    )
    executions: Mapped[list["ScrapingExecution"]] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        order_by="ScrapingExecution.created_at",
    )


class ScrapingBlueprint(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_blueprints"
    __table_args__ = (
        UniqueConstraint("mission_id", "version", name="uq_scraping_blueprint_mission_version"),
        Index("ix_scraping_blueprints_mission_id", "mission_id"),
        Index("ix_scraping_blueprints_status", "status"),
        Index("ix_scraping_blueprints_created_at", "created_at"),
    )

    mission_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_missions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ScrapingBlueprintStatus] = mapped_column(
        Enum(
            ScrapingBlueprintStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=ScrapingBlueprintStatus.GENERATING,
        nullable=False,
    )
    blueprint_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    model_set_id: Mapped[str] = mapped_column(String(64), nullable=False)
    judge_model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    mission: Mapped["ScrapingMission"] = relationship(
        back_populates="blueprints",
        foreign_keys=[mission_id],
    )
    approver: Mapped["User | None"] = relationship(foreign_keys=[approved_by])
    rejecter: Mapped["User | None"] = relationship(foreign_keys=[rejected_by])
    runs: Mapped[list["ScrapingRun"]] = relationship(back_populates="blueprint")


class ScrapingRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_runs"
    __table_args__ = (
        UniqueConstraint("blueprint_id", name="uq_scraping_runs_blueprint_id"),
        Index("ix_scraping_runs_organization_id", "organization_id"),
        Index("ix_scraping_runs_mission_id", "mission_id"),
        Index("ix_scraping_runs_status", "status"),
        Index("ix_scraping_runs_created_at", "created_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    mission_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_missions.id", ondelete="CASCADE"), nullable=False
    )
    blueprint_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_blueprints.id"), nullable=False
    )
    model_set_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ScrapingRunStatus] = mapped_column(
        Enum(
            ScrapingRunStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=ScrapingRunStatus.PLANNING,
        nullable=False,
    )
    recommended_agent_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planner_model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    planner_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped["Organization"] = relationship()
    mission: Mapped["ScrapingMission"] = relationship(back_populates="runs")
    blueprint: Mapped["ScrapingBlueprint"] = relationship(back_populates="runs")
    agents: Mapped[list["ScrapingRunAgent"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ScrapingRunAgent.sequence",
    )
    executions: Mapped[list["ScrapingExecution"]] = relationship(
        back_populates="team_plan",
        cascade="all, delete-orphan",
        order_by="ScrapingExecution.created_at",
    )


class ScrapingRunAgent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_run_agents"
    __table_args__ = (
        Index("ix_scraping_run_agents_run_id", "run_id"),
        Index("ix_scraping_run_agents_run_sequence", "run_id", "sequence"),
    )

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_runs.id", ondelete="CASCADE"), nullable=False
    )
    parent_agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_run_agents.id", ondelete="SET NULL"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ScrapingRunAgentStatus] = mapped_column(
        Enum(
            ScrapingRunAgentStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=ScrapingRunAgentStatus.PLANNED,
        nullable=False,
    )
    dependency_agent_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    run: Mapped["ScrapingRun"] = relationship(back_populates="agents")
    parent_agent: Mapped["ScrapingRunAgent | None"] = relationship(
        remote_side="ScrapingRunAgent.id"
    )
    execution_agents: Mapped[list["ScrapingExecutionAgent"]] = relationship(
        back_populates="team_agent"
    )


class ScrapingExecution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_executions"
    __table_args__ = (
        Index("ix_scraping_executions_organization_id", "organization_id"),
        Index("ix_scraping_executions_mission_id", "mission_id"),
        Index("ix_scraping_executions_blueprint_id", "blueprint_id"),
        Index("ix_scraping_executions_team_plan_id", "team_plan_id"),
        Index("ix_scraping_executions_status", "status"),
        Index("ix_scraping_executions_created_at", "created_at"),
        Index(
            "uq_scraping_executions_active_team_plan",
            "team_plan_id",
            unique=True,
            postgresql_where=text("status in ('queued', 'running', 'cancel_requested')"),
            sqlite_where=text("status in ('queued', 'running', 'cancel_requested')"),
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    mission_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_missions.id", ondelete="CASCADE"), nullable=False
    )
    blueprint_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_blueprints.id"), nullable=False
    )
    team_plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_runs.id", ondelete="CASCADE"), nullable=False
    )
    execution_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[ScrapingExecutionStatus] = mapped_column(
        Enum(
            ScrapingExecutionStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=ScrapingExecutionStatus.QUEUED,
        nullable=False,
    )
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    country_name: Mapped[str] = mapped_column(String(120), nullable=False)
    country_profile_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sources_discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    documents_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_extracted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_verified: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicates_detected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked_sources: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    coverage_debt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    organization: Mapped["Organization"] = relationship()
    mission: Mapped["ScrapingMission"] = relationship(back_populates="executions")
    blueprint: Mapped["ScrapingBlueprint"] = relationship()
    team_plan: Mapped["ScrapingRun"] = relationship(back_populates="executions")
    execution_agents: Mapped[list["ScrapingExecutionAgent"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ScrapingExecutionAgent.created_at",
    )
    coverage_cells: Mapped[list["ScrapingCoverageCell"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ScrapingCoverageCell.created_at",
    )
    tasks: Mapped[list["ScrapingTask"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ScrapingTask.created_at",
    )
    events: Mapped[list["ScrapingEvent"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ScrapingEvent.sequence_number",
    )
    rehabilitation_facilities: Mapped[list["RehabilitationFacility"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="RehabilitationFacility.stable_key",
    )
    rehabilitation_sources: Mapped[list["RehabilitationSource"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="RehabilitationSource.created_at",
    )
    source_retrieval_attempts: Mapped[list["ScrapingSourceRetrievalAttempt"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ScrapingSourceRetrievalAttempt.started_at",
    )
    source_documents: Mapped[list["ScrapingSourceDocument"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ScrapingSourceDocument.retrieval_timestamp",
    )


class ScrapingExecutionAgent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_execution_agents"
    __table_args__ = (
        UniqueConstraint("execution_id", "team_agent_id", name="uq_execution_agent_team_agent"),
        Index("ix_scraping_execution_agents_execution_id", "execution_id"),
        Index("ix_scraping_execution_agents_team_agent_id", "team_agent_id"),
        Index("ix_scraping_execution_agents_status", "status"),
    )

    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    team_agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_run_agents.id"), nullable=False
    )
    status: Mapped[ScrapingExecutionAgentStatus] = mapped_column(
        Enum(
            ScrapingExecutionAgentStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=ScrapingExecutionAgentStatus.WAITING,
        nullable=False,
    )
    current_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    execution: Mapped["ScrapingExecution"] = relationship(back_populates="execution_agents")
    team_agent: Mapped["ScrapingRunAgent"] = relationship(back_populates="execution_agents")
    coverage_cells: Mapped[list["ScrapingCoverageCell"]] = relationship(
        back_populates="assigned_execution_agent"
    )
    tasks: Mapped[list["ScrapingTask"]] = relationship(back_populates="execution_agent")


class ScrapingCoverageCell(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_coverage_cells"
    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "region_name",
            "language_name",
            "source_category",
            name="uq_scraping_coverage_cell_matrix",
        ),
        Index("ix_scraping_coverage_cells_execution_id", "execution_id"),
        Index("ix_scraping_coverage_cells_status", "status"),
        Index("ix_scraping_coverage_cells_assigned_agent", "assigned_execution_agent_id"),
    )

    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    region_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region_name: Mapped[str] = mapped_column(Text, nullable=False)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_category: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ScrapingCoverageStatus] = mapped_column(
        Enum(
            ScrapingCoverageStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=ScrapingCoverageStatus.NOT_STARTED,
        nullable=False,
    )
    assigned_execution_agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_execution_agents.id", ondelete="SET NULL"), nullable=True
    )
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    execution: Mapped["ScrapingExecution"] = relationship(back_populates="coverage_cells")
    assigned_execution_agent: Mapped["ScrapingExecutionAgent | None"] = relationship(
        back_populates="coverage_cells"
    )
    tasks: Mapped[list["ScrapingTask"]] = relationship(back_populates="coverage_cell")


class ScrapingTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_tasks"
    __table_args__ = (
        Index("ix_scraping_tasks_execution_id", "execution_id"),
        Index("ix_scraping_tasks_execution_agent_id", "execution_agent_id"),
        Index("ix_scraping_tasks_coverage_cell_id", "coverage_cell_id"),
        Index("ix_scraping_tasks_status", "status"),
        Index("ix_scraping_tasks_task_type", "task_type"),
        Index("ix_scraping_tasks_priority", "priority"),
    )

    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    execution_agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_execution_agents.id", ondelete="CASCADE"), nullable=False
    )
    coverage_cell_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_coverage_cells.id", ondelete="SET NULL"), nullable=True
    )
    parent_task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_tasks.id", ondelete="SET NULL"), nullable=True
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ScrapingTaskStatus] = mapped_column(
        Enum(
            ScrapingTaskStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=ScrapingTaskStatus.QUEUED,
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    current_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    dependency_task_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    execution: Mapped["ScrapingExecution"] = relationship(back_populates="tasks")
    execution_agent: Mapped["ScrapingExecutionAgent"] = relationship(back_populates="tasks")
    coverage_cell: Mapped["ScrapingCoverageCell | None"] = relationship(back_populates="tasks")
    parent_task: Mapped["ScrapingTask | None"] = relationship(remote_side="ScrapingTask.id")


class ScrapingEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "scraping_events"
    __table_args__ = (
        UniqueConstraint("execution_id", "sequence_number", name="uq_scraping_event_sequence"),
        Index("ix_scraping_events_execution_sequence", "execution_id", "sequence_number"),
        Index("ix_scraping_events_execution_agent_id", "execution_agent_id"),
        Index("ix_scraping_events_task_id", "task_id"),
        Index("ix_scraping_events_event_type", "event_type"),
        Index("ix_scraping_events_created_at", "created_at"),
    )

    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    execution_agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_execution_agents.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_tasks.id", ondelete="SET NULL"), nullable=True
    )
    coverage_cell_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_coverage_cells.id", ondelete="SET NULL"), nullable=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    execution: Mapped["ScrapingExecution"] = relationship(back_populates="events")


class ScrapingSourceDiscoveryQuery(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_source_discovery_queries"
    __table_args__ = (
        CheckConstraint("length(trim(query_text)) > 0", name="ck_source_discovery_query_not_blank"),
        CheckConstraint("result_count >= 0", name="ck_source_discovery_query_result_count"),
        Index("ix_source_discovery_queries_org", "organization_id"),
        Index("ix_source_discovery_queries_execution", "execution_id"),
        Index("ix_source_discovery_queries_coverage", "coverage_cell_id"),
        Index("ix_source_discovery_queries_task", "task_id"),
        Index("ix_source_discovery_queries_provider", "provider"),
        Index("ix_source_discovery_queries_status", "status"),
        Index(
            "ix_source_discovery_queries_context",
            "organization_id",
            "execution_id",
            "coverage_cell_id",
            "provider",
            "source_category",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    execution_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=True
    )
    coverage_cell_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_coverage_cells.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_tasks.id", ondelete="SET NULL"), nullable=True
    )
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    country_name: Mapped[str] = mapped_column(String(120), nullable=False)
    region_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region_name: Mapped[str] = mapped_column(String(160), nullable=False)
    language_code: Mapped[str] = mapped_column(String(16), nullable=False)
    language_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_category: Mapped[str] = mapped_column(String(120), nullable=False)
    query_text: Mapped[str] = mapped_column(String(512), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[SourceDiscoveryQueryStatus] = mapped_column(
        Enum(
            SourceDiscoveryQueryStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=SourceDiscoveryQueryStatus.PENDING,
        nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    organization: Mapped["Organization"] = relationship()
    execution: Mapped["ScrapingExecution | None"] = relationship()
    coverage_cell: Mapped["ScrapingCoverageCell | None"] = relationship()
    task: Mapped["ScrapingTask | None"] = relationship()
    candidates: Mapped[list["ScrapingSourceCandidate"]] = relationship(
        back_populates="discovery_query",
        cascade="all, delete-orphan",
        order_by="ScrapingSourceCandidate.rank",
    )


class ScrapingSourceCandidate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_source_candidates"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "discovery_query_id",
            "canonical_url",
            name="uq_source_candidate_query_url",
        ),
        CheckConstraint("rank >= 1", name="ck_source_candidate_rank"),
        CheckConstraint(
            "initial_relevance_score >= 0 AND initial_relevance_score <= 1",
            name="ck_source_candidate_relevance_score",
        ),
        CheckConstraint(
            "(lower(url) LIKE 'http://%' OR lower(url) LIKE 'https://%') AND "
            "(lower(canonical_url) LIKE 'http://%' OR lower(canonical_url) LIKE 'https://%')",
            name="ck_source_candidate_http_urls",
        ),
        Index("ix_source_candidates_org", "organization_id"),
        Index("ix_source_candidates_execution", "execution_id"),
        Index("ix_source_candidates_coverage", "coverage_cell_id"),
        Index("ix_source_candidates_query", "discovery_query_id"),
        Index("ix_source_candidates_provider", "provider"),
        Index("ix_source_candidates_domain", "domain"),
        Index("ix_source_candidates_status", "status"),
        Index("ix_source_candidates_canonical_url", "canonical_url"),
        Index(
            "ix_source_candidates_context_url",
            "organization_id",
            "execution_id",
            "coverage_cell_id",
            "canonical_url",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    execution_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=True
    )
    coverage_cell_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_coverage_cells.id", ondelete="SET NULL"), nullable=True
    )
    discovery_query_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("scraping_source_discovery_queries.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_result_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    snippet: Mapped[str] = mapped_column(String(1000), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    country_name: Mapped[str] = mapped_column(String(120), nullable=False)
    region_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region_name: Mapped[str] = mapped_column(String(160), nullable=False)
    language_code: Mapped[str] = mapped_column(String(16), nullable=False)
    language_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_category: Mapped[str] = mapped_column(String(120), nullable=False)
    initial_relevance_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    initial_trust_tier: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[SourceCandidateStatus] = mapped_column(
        Enum(
            SourceCandidateStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=SourceCandidateStatus.DISCOVERED,
        nullable=False,
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    organization: Mapped["Organization"] = relationship()
    execution: Mapped["ScrapingExecution | None"] = relationship()
    coverage_cell: Mapped["ScrapingCoverageCell | None"] = relationship()
    discovery_query: Mapped["ScrapingSourceDiscoveryQuery"] = relationship(
        back_populates="candidates"
    )
    retrieval_attempts: Mapped[list["ScrapingSourceRetrievalAttempt"]] = relationship(
        back_populates="source_candidate",
        cascade="all, delete-orphan",
        order_by="ScrapingSourceRetrievalAttempt.started_at",
    )
    source_documents: Mapped[list["ScrapingSourceDocument"]] = relationship(
        back_populates="source_candidate",
        cascade="all, delete-orphan",
        order_by="ScrapingSourceDocument.retrieval_timestamp",
    )


class ScrapingSourceRetrievalAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_source_retrieval_attempts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_source_retrieval_attempt_idempotency",
        ),
        CheckConstraint("redirect_count >= 0", name="ck_source_retrieval_attempt_redirect_count"),
        CheckConstraint(
            "bytes_received IS NULL OR bytes_received >= 0",
            name="ck_source_retrieval_attempt_bytes_received",
        ),
        Index("ix_source_retrieval_attempts_org", "organization_id"),
        Index("ix_source_retrieval_attempts_execution", "execution_id"),
        Index("ix_source_retrieval_attempts_candidate", "source_candidate_id"),
        Index("ix_source_retrieval_attempts_coverage", "coverage_cell_id"),
        Index("ix_source_retrieval_attempts_task", "task_id"),
        Index("ix_source_retrieval_attempts_status", "status"),
        Index(
            "ix_source_retrieval_attempts_context",
            "organization_id",
            "execution_id",
            "source_candidate_id",
            "status",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    source_candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_candidates.id", ondelete="CASCADE"), nullable=False
    )
    coverage_cell_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_coverage_cells.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_tasks.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[SourceRetrievalAttemptStatus] = mapped_column(
        Enum(
            SourceRetrievalAttemptStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        nullable=False,
    )
    requested_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    final_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    redirect_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    declared_content_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_received: Mapped[int | None] = mapped_column(Integer, nullable=True)
    robots_status: Mapped[SourceRetrievalRobotsStatus | None] = mapped_column(
        Enum(
            SourceRetrievalRobotsStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        nullable=True,
    )
    failure_classification: Mapped[str | None] = mapped_column(String(80), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    organization: Mapped["Organization"] = relationship()
    execution: Mapped["ScrapingExecution"] = relationship(back_populates="source_retrieval_attempts")
    source_candidate: Mapped["ScrapingSourceCandidate"] = relationship(
        back_populates="retrieval_attempts"
    )
    coverage_cell: Mapped["ScrapingCoverageCell | None"] = relationship()
    task: Mapped["ScrapingTask | None"] = relationship()
    source_documents: Mapped[list["ScrapingSourceDocument"]] = relationship(
        back_populates="retrieval_attempt",
        order_by="ScrapingSourceDocument.retrieval_timestamp",
    )


class ScrapingSourceDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_source_documents"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_candidate_id",
            "content_sha256",
            name="uq_source_document_candidate_hash",
        ),
        UniqueConstraint("retrieval_attempt_id", name="uq_source_document_retrieval_attempt"),
        CheckConstraint("byte_size >= 0", name="ck_source_document_byte_size"),
        Index("ix_source_documents_org", "organization_id"),
        Index("ix_source_documents_execution", "execution_id"),
        Index("ix_source_documents_candidate", "source_candidate_id"),
        Index("ix_source_documents_attempt", "retrieval_attempt_id"),
        Index("ix_source_documents_hash", "content_sha256"),
        Index("ix_source_documents_retrieved", "retrieval_timestamp"),
        Index(
            "ix_source_documents_context",
            "organization_id",
            "execution_id",
            "source_candidate_id",
            "retrieval_timestamp",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    source_candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_candidates.id", ondelete="CASCADE"), nullable=False
    )
    retrieval_attempt_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("scraping_source_retrieval_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    final_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    charset: Mapped[str | None] = mapped_column(String(80), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    organization: Mapped["Organization"] = relationship()
    execution: Mapped["ScrapingExecution"] = relationship(back_populates="source_documents")
    source_candidate: Mapped["ScrapingSourceCandidate"] = relationship(
        back_populates="source_documents"
    )
    retrieval_attempt: Mapped["ScrapingSourceRetrievalAttempt"] = relationship(
        back_populates="source_documents"
    )


class ScrapingSourceDocumentText(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_source_document_texts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_document_id",
            "parser_version",
            "source_content_hash",
            name="uq_source_document_text_version",
        ),
        CheckConstraint("character_count >= 0", name="ck_source_document_text_character_count"),
        CheckConstraint(
            "original_character_count >= 0",
            name="ck_source_document_text_original_character_count",
        ),
        Index("ix_source_document_texts_org", "organization_id"),
        Index("ix_source_document_texts_execution", "execution_id"),
        Index("ix_source_document_texts_document", "source_document_id"),
        Index("ix_source_document_texts_candidate", "source_candidate_id"),
        Index("ix_source_document_texts_coverage", "coverage_cell_id"),
        Index("ix_source_document_texts_hash", "prepared_text_hash"),
        Index("ix_source_document_texts_status", "preparation_status"),
    )

    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_documents.id", ondelete="CASCADE"), nullable=False
    )
    source_candidate_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_source_candidates.id", ondelete="SET NULL"), nullable=True
    )
    coverage_cell_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_coverage_cells.id", ondelete="SET NULL"), nullable=True
    )
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prepared_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    prepared_text: Mapped[str] = mapped_column(Text, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    original_character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    preparation_status: Mapped[SourceDocumentTextPreparationStatus] = mapped_column(
        Enum(
            SourceDocumentTextPreparationStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        nullable=False,
    )
    failure_classification: Mapped[str | None] = mapped_column(String(80), nullable=True)

    organization: Mapped["Organization"] = relationship()
    execution: Mapped["ScrapingExecution"] = relationship()
    source_document: Mapped["ScrapingSourceDocument"] = relationship()
    source_candidate: Mapped["ScrapingSourceCandidate | None"] = relationship()
    coverage_cell: Mapped["ScrapingCoverageCell | None"] = relationship()


class ScrapingSourceDocumentChunk(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "scraping_source_document_chunks"
    __table_args__ = (
        UniqueConstraint("prepared_text_id", "chunk_index", name="uq_source_document_chunk_index"),
        UniqueConstraint("prepared_text_id", "chunk_hash", name="uq_source_document_chunk_hash"),
        CheckConstraint("chunk_index >= 0", name="ck_source_document_chunk_index"),
        CheckConstraint("character_start >= 0", name="ck_source_document_chunk_start"),
        CheckConstraint("character_end > character_start", name="ck_source_document_chunk_end"),
        Index("ix_source_document_chunks_org", "organization_id"),
        Index("ix_source_document_chunks_execution", "execution_id"),
        Index("ix_source_document_chunks_document", "source_document_id"),
        Index("ix_source_document_chunks_prepared", "prepared_text_id"),
        Index("ix_source_document_chunks_coverage", "coverage_cell_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_documents.id", ondelete="CASCADE"), nullable=False
    )
    prepared_text_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_document_texts.id", ondelete="CASCADE"), nullable=False
    )
    coverage_cell_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_coverage_cells.id", ondelete="SET NULL"), nullable=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    character_start: Mapped[int] = mapped_column(Integer, nullable=False)
    character_end: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    organization: Mapped["Organization"] = relationship()
    execution: Mapped["ScrapingExecution"] = relationship()
    source_document: Mapped["ScrapingSourceDocument"] = relationship()
    prepared_text: Mapped["ScrapingSourceDocumentText"] = relationship()
    coverage_cell: Mapped["ScrapingCoverageCell | None"] = relationship()


class ScrapingFacilityExtractionAttempt(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "scraping_facility_extraction_attempts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_facility_extraction_attempt_idempotency",
        ),
        CheckConstraint("attempt_number >= 1", name="ck_facility_extraction_attempt_number"),
        CheckConstraint(
            "input_character_count >= 0",
            name="ck_facility_extraction_attempt_input_character_count",
        ),
        CheckConstraint(
            "output_candidate_count >= 0",
            name="ck_facility_extraction_attempt_output_candidate_count",
        ),
        Index("ix_facility_extraction_attempts_org", "organization_id"),
        Index("ix_facility_extraction_attempts_execution", "execution_id"),
        Index("ix_facility_extraction_attempts_document", "source_document_id"),
        Index("ix_facility_extraction_attempts_chunk", "chunk_id"),
        Index("ix_facility_extraction_attempts_coverage", "coverage_cell_id"),
        Index("ix_facility_extraction_attempts_status", "status"),
        Index("ix_facility_extraction_attempts_context", "organization_id", "execution_id", "chunk_id", "status"),
    )

    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_documents.id", ondelete="CASCADE"), nullable=False
    )
    prepared_text_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_document_texts.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_document_chunks.id", ondelete="CASCADE"), nullable=False
    )
    coverage_cell_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_coverage_cells.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[FacilityExtractionAttemptStatus] = mapped_column(
        Enum(
            FacilityExtractionAttemptStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_character_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_classification: Mapped[str | None] = mapped_column(String(80), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ScrapingFacilityCandidate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_facility_candidates"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "extraction_attempt_id",
            "candidate_fingerprint",
            name="uq_facility_candidate_attempt_fingerprint",
        ),
        CheckConstraint("length(trim(raw_name)) > 0", name="ck_facility_candidate_raw_name"),
        CheckConstraint(
            "model_confidence IS NULL OR (model_confidence >= 0 AND model_confidence <= 1)",
            name="ck_facility_candidate_model_confidence",
        ),
        Index("ix_facility_candidates_org", "organization_id"),
        Index("ix_facility_candidates_execution", "execution_id"),
        Index("ix_facility_candidates_coverage", "coverage_cell_id"),
        Index("ix_facility_candidates_document", "source_document_id"),
        Index("ix_facility_candidates_chunk", "chunk_id"),
        Index("ix_facility_candidates_attempt", "extraction_attempt_id"),
        Index("ix_facility_candidates_status", "staging_status"),
        Index("ix_facility_candidates_fingerprint", "candidate_fingerprint"),
    )

    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    coverage_cell_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_coverage_cells.id", ondelete="SET NULL"), nullable=True
    )
    source_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_documents.id", ondelete="CASCADE"), nullable=False
    )
    prepared_text_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_document_texts.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_document_chunks.id", ondelete="CASCADE"), nullable=False
    )
    extraction_attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_facility_extraction_attempts.id", ondelete="CASCADE"), nullable=False
    )
    raw_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    model_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    staging_status: Mapped[FacilityCandidateStagingStatus] = mapped_column(
        Enum(
            FacilityCandidateStagingStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=FacilityCandidateStagingStatus.EXTRACTED,
        nullable=False,
    )
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class ScrapingFacilityCandidateEvidence(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "scraping_facility_candidate_evidence"
    __table_args__ = (
        UniqueConstraint(
            "facility_candidate_id",
            "field_name",
            "evidence_hash",
            name="uq_facility_candidate_evidence_field_hash",
        ),
        CheckConstraint("quote_start >= 0", name="ck_facility_candidate_evidence_quote_start"),
        CheckConstraint("quote_end > quote_start", name="ck_facility_candidate_evidence_quote_end"),
        CheckConstraint("length(evidence_quote) <= 1000", name="ck_facility_candidate_evidence_quote_length"),
        Index("ix_facility_candidate_evidence_org", "organization_id"),
        Index("ix_facility_candidate_evidence_execution", "execution_id"),
        Index("ix_facility_candidate_evidence_candidate", "facility_candidate_id"),
        Index("ix_facility_candidate_evidence_document", "source_document_id"),
        Index("ix_facility_candidate_evidence_chunk", "chunk_id"),
        Index("ix_facility_candidate_evidence_field", "field_name"),
        Index("ix_facility_candidate_evidence_status", "verification_status"),
    )

    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    facility_candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_facility_candidates.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_documents.id", ondelete="CASCADE"), nullable=False
    )
    prepared_text_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_document_texts.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_source_document_chunks.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    evidence_quote: Mapped[str] = mapped_column(String(1000), nullable=False)
    quote_start: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_end: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_status: Mapped[FacilityCandidateEvidenceVerificationStatus] = mapped_column(
        Enum(
            FacilityCandidateEvidenceVerificationStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ScrapingFacilityCandidatePublication(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_facility_candidate_publications"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "facility_candidate_id",
            name="uq_facility_candidate_publication_candidate",
        ),
        CheckConstraint(
            "status != 'published' OR final_facility_id IS NOT NULL",
            name="ck_facility_candidate_publication_published_facility",
        ),
        Index("ix_facility_candidate_publications_org", "organization_id"),
        Index("ix_facility_candidate_publications_execution", "execution_id"),
        Index("ix_facility_candidate_publications_candidate", "facility_candidate_id"),
        Index("ix_facility_candidate_publications_facility", "final_facility_id"),
        Index("ix_facility_candidate_publications_status", "status"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    facility_candidate_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("scraping_facility_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    final_facility_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("rehabilitation_facilities.id", ondelete="SET NULL"),
        nullable=True,
    )
    normalization_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[FacilityCandidatePublicationStatus] = mapped_column(
        Enum(
            FacilityCandidatePublicationStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=FacilityCandidatePublicationStatus.PENDING,
        nullable=False,
    )
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization: Mapped["Organization"] = relationship()
    execution: Mapped["ScrapingExecution"] = relationship()
    facility_candidate: Mapped["ScrapingFacilityCandidate"] = relationship()
    final_facility: Mapped["RehabilitationFacility | None"] = relationship()


CONFIDENCE_CHECK = "confidence_score >= 0 AND confidence_score <= 1"


class RehabilitationFacility(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "rehabilitation_facilities"
    __table_args__ = (
        UniqueConstraint("execution_id", "stable_key", name="uq_rehab_facility_execution_key"),
        CheckConstraint(CONFIDENCE_CHECK, name="ck_rehab_facility_confidence_score"),
        Index("ix_rehab_facilities_execution_id", "execution_id"),
        Index("ix_rehab_facilities_organization_id", "organization_id"),
        Index("ix_rehab_facilities_verification_status", "verification_status"),
        Index("ix_rehab_facilities_country_region", "country_code", "primary_region"),
    )

    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_language_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    facility_type: Mapped[str] = mapped_column(String(80), nullable=False)
    organization_type: Mapped[str] = mapped_column(String(80), nullable=False)
    operational_status: Mapped[str] = mapped_column(String(80), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    country_name: Mapped[str] = mapped_column(String(120), nullable=False)
    primary_region: Mapped[str | None] = mapped_column(String(160), nullable=True)
    primary_city: Mapped[str | None] = mapped_column(String(160), nullable=True)
    primary_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    primary_website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    duplicate_status: Mapped[str] = mapped_column(String(80), nullable=False)
    human_review_status: Mapped[str] = mapped_column(String(80), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    execution: Mapped["ScrapingExecution"] = relationship(back_populates="rehabilitation_facilities")
    organization: Mapped["Organization"] = relationship()
    aliases: Mapped[list["RehabilitationFacilityAlias"]] = relationship(
        back_populates="facility", cascade="all, delete-orphan", order_by="RehabilitationFacilityAlias.name"
    )
    locations: Mapped[list["RehabilitationFacilityLocation"]] = relationship(
        back_populates="facility", cascade="all, delete-orphan", order_by="RehabilitationFacilityLocation.created_at"
    )
    contacts: Mapped[list["RehabilitationFacilityContact"]] = relationship(
        back_populates="facility", cascade="all, delete-orphan", order_by="RehabilitationFacilityContact.created_at"
    )
    attributes: Mapped[list["RehabilitationFacilityAttribute"]] = relationship(
        back_populates="facility", cascade="all, delete-orphan", order_by="RehabilitationFacilityAttribute.created_at"
    )
    staff: Mapped[list["RehabilitationFacilityStaff"]] = relationship(
        back_populates="facility", cascade="all, delete-orphan", order_by="RehabilitationFacilityStaff.created_at"
    )
    licenses: Mapped[list["RehabilitationFacilityLicense"]] = relationship(
        back_populates="facility", cascade="all, delete-orphan", order_by="RehabilitationFacilityLicense.created_at"
    )
    operating_hours: Mapped[list["RehabilitationFacilityOperatingHours"]] = relationship(
        back_populates="facility",
        cascade="all, delete-orphan",
        order_by="RehabilitationFacilityOperatingHours.day_of_week",
    )
    source_links: Mapped[list["RehabilitationFacilitySourceLink"]] = relationship(
        back_populates="facility", cascade="all, delete-orphan", order_by="RehabilitationFacilitySourceLink.created_at"
    )
    evidence: Mapped[list["RehabilitationFieldEvidence"]] = relationship(
        back_populates="facility", cascade="all, delete-orphan", order_by="RehabilitationFieldEvidence.created_at"
    )
    unresolved_fields: Mapped[list["RehabilitationUnresolvedField"]] = relationship(
        back_populates="facility", cascade="all, delete-orphan", order_by="RehabilitationUnresolvedField.created_at"
    )


class RehabilitationFacilityAlias(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "rehabilitation_facility_aliases"
    __table_args__ = (
        UniqueConstraint("facility_id", "name", "alias_type", name="uq_rehab_alias_facility_name_type"),
        Index("ix_rehab_aliases_facility_id", "facility_id"),
    )

    facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    alias_type: Mapped[str] = mapped_column(String(40), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    facility: Mapped["RehabilitationFacility"] = relationship(back_populates="aliases")


class RehabilitationFacilityLocation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "rehabilitation_facility_locations"
    __table_args__ = (
        UniqueConstraint("facility_id", "location_type", "location_name", name="uq_rehab_location_identity"),
        CheckConstraint(CONFIDENCE_CHECK, name="ck_rehab_location_confidence_score"),
        Index("ix_rehab_locations_facility_id", "facility_id"),
    )

    facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    location_type: Mapped[str] = mapped_column(String(60), nullable=False)
    location_name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    country_name: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str | None] = mapped_column(String(160), nullable=True)
    district: Mapped[str | None] = mapped_column(String(160), nullable=True)
    city: Mapped[str | None] = mapped_column(String(160), nullable=True)
    area: Mapped[str | None] = mapped_column(String(160), nullable=True)
    full_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    facility: Mapped["RehabilitationFacility"] = relationship(back_populates="locations")


class RehabilitationFacilityContact(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "rehabilitation_facility_contacts"
    __table_args__ = (
        UniqueConstraint("facility_id", "contact_type", "value", name="uq_rehab_contact_facility_type_value"),
        CheckConstraint(CONFIDENCE_CHECK, name="ck_rehab_contact_confidence_score"),
        Index("ix_rehab_contacts_facility_id", "facility_id"),
        Index("ix_rehab_contacts_type", "contact_type"),
    )

    facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    contact_type: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    available_24_7: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    facility: Mapped["RehabilitationFacility"] = relationship(back_populates="contacts")


class RehabilitationFacilityAttribute(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "rehabilitation_facility_attributes"
    __table_args__ = (
        UniqueConstraint("facility_id", "attribute_group", "attribute_key", name="uq_rehab_attribute_identity"),
        CheckConstraint(CONFIDENCE_CHECK, name="ck_rehab_attribute_confidence_score"),
        Index("ix_rehab_attributes_facility_id", "facility_id"),
        Index("ix_rehab_attributes_group", "attribute_group"),
    )

    facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    attribute_group: Mapped[str] = mapped_column(String(80), nullable=False)
    attribute_key: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    value_type: Mapped[str] = mapped_column(String(40), nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value_number: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    value_unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    period: Mapped[str | None] = mapped_column(String(40), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    facility: Mapped["RehabilitationFacility"] = relationship(back_populates="attributes")


class RehabilitationFacilityStaff(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "rehabilitation_facility_staff"
    __table_args__ = (
        UniqueConstraint("facility_id", "name", "role", name="uq_rehab_staff_facility_name_role"),
        CheckConstraint(CONFIDENCE_CHECK, name="ck_rehab_staff_confidence_score"),
        Index("ix_rehab_staff_facility_id", "facility_id"),
    )

    facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(160), nullable=False)
    specialty: Mapped[str | None] = mapped_column(String(160), nullable=True)
    credentials: Mapped[str | None] = mapped_column(String(255), nullable=True)
    public_profile_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    facility: Mapped["RehabilitationFacility"] = relationship(back_populates="staff")


class RehabilitationFacilityLicense(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "rehabilitation_facility_licenses"
    __table_args__ = (
        UniqueConstraint("facility_id", "record_type", "identifier", name="uq_rehab_license_identity"),
        CheckConstraint(CONFIDENCE_CHECK, name="ck_rehab_license_confidence_score"),
        Index("ix_rehab_licenses_facility_id", "facility_id"),
    )

    facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    record_type: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    issuing_authority: Mapped[str | None] = mapped_column(String(255), nullable=True)
    identifier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(80), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    facility: Mapped["RehabilitationFacility"] = relationship(back_populates="licenses")


class RehabilitationFacilityOperatingHours(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "rehabilitation_facility_operating_hours"
    __table_args__ = (
        UniqueConstraint("facility_id", "day_of_week", name="uq_rehab_hours_facility_day"),
        Index("ix_rehab_hours_facility_id", "facility_id"),
    )

    facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    opens_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    closes_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_24_hours: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    facility: Mapped["RehabilitationFacility"] = relationship(back_populates="operating_hours")


class RehabilitationSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "rehabilitation_sources"
    __table_args__ = (
        UniqueConstraint("execution_id", "canonical_url", name="uq_rehab_source_execution_url"),
        Index("ix_rehab_sources_execution_id", "execution_id"),
        Index("ix_rehab_sources_coverage_cell_id", "coverage_cell_id"),
        Index("ix_rehab_sources_task_id", "task_id"),
        Index("ix_rehab_sources_fetch_status", "fetch_status"),
    )

    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    coverage_cell_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_coverage_cells.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_tasks.id", ondelete="SET NULL"), nullable=True
    )
    original_url: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(512), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    source_category: Mapped[str] = mapped_column(String(120), nullable=False)
    discovery_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    region: Mapped[str | None] = mapped_column(String(160), nullable=True)
    fetch_status: Mapped[str] = mapped_column(String(80), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    execution: Mapped["ScrapingExecution"] = relationship(back_populates="rehabilitation_sources")
    coverage_cell: Mapped["ScrapingCoverageCell | None"] = relationship()
    task: Mapped["ScrapingTask | None"] = relationship()
    facility_links: Mapped[list["RehabilitationFacilitySourceLink"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", order_by="RehabilitationFacilitySourceLink.created_at"
    )
    evidence: Mapped[list["RehabilitationFieldEvidence"]] = relationship(back_populates="source")


class RehabilitationFacilitySourceLink(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "rehabilitation_facility_source_links"
    __table_args__ = (
        UniqueConstraint("facility_id", "source_id", "relationship_type", name="uq_rehab_source_link_identity"),
        Index("ix_rehab_source_links_facility_id", "facility_id"),
        Index("ix_rehab_source_links_source_id", "source_id"),
    )

    facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_sources.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(80), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    facility: Mapped["RehabilitationFacility"] = relationship(back_populates="source_links")
    source: Mapped["RehabilitationSource"] = relationship(back_populates="facility_links")


class RehabilitationFieldEvidence(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "rehabilitation_field_evidence"
    __table_args__ = (
        UniqueConstraint("facility_id", "source_id", "field_path", name="uq_rehab_evidence_field_source"),
        CheckConstraint(CONFIDENCE_CHECK, name="ck_rehab_evidence_confidence_score"),
        Index("ix_rehab_evidence_facility_id", "facility_id"),
        Index("ix_rehab_evidence_source_id", "source_id"),
    )

    facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("rehabilitation_sources.id", ondelete="SET NULL"), nullable=True
    )
    field_path: Mapped[str] = mapped_column(String(255), nullable=False)
    extracted_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    page_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url_snapshot: Mapped[str | None] = mapped_column(String(512), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(80), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    facility: Mapped["RehabilitationFacility"] = relationship(back_populates="evidence")
    source: Mapped["RehabilitationSource | None"] = relationship(back_populates="evidence")


class RehabilitationPossibleDuplicate(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "rehabilitation_possible_duplicates"
    __table_args__ = (
        UniqueConstraint("execution_id", "left_facility_id", "right_facility_id", name="uq_rehab_duplicate_pair"),
        CheckConstraint("left_facility_id < right_facility_id", name="ck_rehab_duplicate_ordered_pair"),
        CheckConstraint("match_score >= 0 AND match_score <= 1", name="ck_rehab_duplicate_match_score"),
        Index("ix_rehab_duplicates_execution_id", "execution_id"),
        Index("ix_rehab_duplicates_left_facility_id", "left_facility_id"),
        Index("ix_rehab_duplicates_right_facility_id", "right_facility_id"),
    )

    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    left_facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    right_facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    match_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    matching_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_status: Mapped[str] = mapped_column(String(80), nullable=False)
    resolved_facility_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    execution: Mapped["ScrapingExecution"] = relationship()
    left_facility: Mapped["RehabilitationFacility"] = relationship(foreign_keys=[left_facility_id])
    right_facility: Mapped["RehabilitationFacility"] = relationship(foreign_keys=[right_facility_id])
    resolved_facility: Mapped["RehabilitationFacility | None"] = relationship(foreign_keys=[resolved_facility_id])


class RehabilitationUnresolvedField(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "rehabilitation_unresolved_fields"
    __table_args__ = (
        UniqueConstraint("facility_id", "field_path", "unresolved_status", name="uq_rehab_unresolved_identity"),
        Index("ix_rehab_unresolved_facility_id", "facility_id"),
        Index("ix_rehab_unresolved_source_id", "source_id"),
        Index("ix_rehab_unresolved_status", "unresolved_status"),
    )

    facility_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rehabilitation_facilities.id", ondelete="CASCADE"), nullable=False
    )
    field_path: Mapped[str] = mapped_column(String(255), nullable=False)
    unresolved_status: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_follow_up: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("rehabilitation_sources.id", ondelete="SET NULL"), nullable=True
    )
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    facility: Mapped["RehabilitationFacility"] = relationship(back_populates="unresolved_fields")
    source: Mapped["RehabilitationSource | None"] = relationship()


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
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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


class SavedVerdict(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "saved_verdicts"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "user_id", "source_verdict_id", name="uq_saved_verdict_user_source"
        ),
        Index("ix_saved_verdicts_org_user_saved_at", "org_id", "user_id", "saved_at"),
    )

    org_id: Mapped[str] = UuidFK("organizations")
    user_id: Mapped[str] = UuidFK("users")
    source_verdict_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_chat_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_chat_title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_user_message: Mapped[str] = mapped_column(Text, nullable=False)
    verdict_text: Mapped[str] = mapped_column(Text, nullable=False)
    verdict_reason: Mapped[str] = mapped_column(Text, nullable=False)
    verdict_model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[Strategy] = mapped_column(Enum(Strategy), nullable=False)
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VerdictLesson(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Structured lesson built when a user disagrees with the AI verdict."""

    __tablename__ = "verdict_lessons"
    __table_args__ = (UniqueConstraint("turn_id", name="uq_verdict_lesson_turn"),)

    turn_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("turns.id", ondelete="SET NULL"), nullable=True
    )
    chat_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chats.id", ondelete="SET NULL"), nullable=True
    )
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
    discussion_messages: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[LessonStatus] = mapped_column(
        Enum(
            LessonStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=LessonStatus.BUILDING,
        nullable=False,
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


class AuditSeverity(str, enum.Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AuditLog(Base):
    """Immutable enterprise audit trail — every authenticated API action and admin event."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("organizations.id"), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    actor_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    actor_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    action: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    severity: Mapped[AuditSeverity] = mapped_column(
        Enum(AuditSeverity, values_callable=lambda x: [e.value for e in x]),
        default=AuditSeverity.INFO,
        nullable=False,
    )
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    target_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    target_user_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    http_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    http_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
