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
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
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
    QUEUED = "queued"
    RUNNING = "running"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISCARDED = "discarded"
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
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
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


class CrawlNodeSourceClassification(str, enum.Enum):
    OFFICIAL_FACILITY_SITE = "official_facility_site"
    FACILITY_PROFILE = "facility_profile"
    DIRECTORY = "directory"
    REGISTRY = "registry"
    GOVERNMENT_SOURCE = "government_source"
    COMMERCIAL_LISTING = "commercial_listing"
    PDF = "pdf"
    SOCIAL_PROFILE = "social_profile"
    SUPPORTING_SOURCE = "supporting_source"
    IRRELEVANT = "irrelevant"
    UNCLASSIFIED = "unclassified"


class CrawlEdgeRelationshipType(str, enum.Enum):
    DIRECTORY_TO_PROFILE = "directory_to_profile"
    DIRECTORY_TO_OFFICIAL_SITE = "directory_to_official_site"
    PROFILE_TO_OFFICIAL_SITE = "profile_to_official_site"
    PAGINATION = "pagination"
    LOAD_MORE = "load_more"
    STRUCTURED_API = "structured_api"
    OFFICIAL_SITE_TO_CONTACT_PAGE = "official_site_to_contact_page"
    OFFICIAL_SITE_TO_PROGRAM_PAGE = "official_site_to_program_page"
    OFFICIAL_SITE_TO_LOCATION_PAGE = "official_site_to_location_page"
    OFFICIAL_SITE_TO_LICENSING_PAGE = "official_site_to_licensing_page"
    OFFICIAL_SITE_TO_EVIDENCE_PAGE = "official_site_to_evidence_page"
    RELATED_SOURCE = "related_source"
    DISCOVERED_LINK = "discovered_link"


class SourceCandidateStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


class Phase5WorkKind(str, enum.Enum):
    DIRECTORY_EXPANSION = "directory_expansion"
    HTTP_RETRIEVAL = "http_retrieval"
    FIRECRAWL_RETRIEVAL = "firecrawl_retrieval"
    PLAYWRIGHT_RETRIEVAL = "playwright_retrieval"


class Phase5WorkStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    pinned_verdict_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("verdicts.id", ondelete="SET NULL"), nullable=True
    )

    organization: Mapped["Organization"] = relationship(back_populates="chats")
    project: Mapped["Project | None"] = relationship(back_populates="chats")
    turns: Mapped[list["Turn"]] = relationship(back_populates="chat", order_by="Turn.created_at")
    share_links: Mapped[list["ShareLink"]] = relationship(back_populates="chat")
    pinned_verdict: Mapped["Verdict | None"] = relationship(
        "Verdict",
        foreign_keys=[pinned_verdict_id],
        post_update=True,
    )


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
        Index("ix_scraping_missions_country_iso3", "country_iso3"),
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
    country_iso3: Mapped[str | None] = mapped_column(String(3), nullable=True)
    continent: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
        Index("ix_scraping_blueprints_provider", "provider"),
        Index("ix_scraping_blueprints_prompt_template_version", "prompt_template_version"),
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
            length=32,
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
    country_name_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country_iso3_snapshot: Mapped[str | None] = mapped_column(String(3), nullable=True)
    continent_snapshot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_template_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rendered_prompt_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_readable_blueprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_blueprint: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    revision_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_execution_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

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
            postgresql_where=text(
                "status in ('queued', 'running', 'pause_requested', 'paused', 'cancel_requested')"
            ),
            sqlite_where=text(
                "status in ('queued', 'running', 'pause_requested', 'paused', 'cancel_requested')"
            ),
        ),
        Index(
            "uq_scraping_executions_active_mission_campaign",
            "mission_id",
            unique=True,
            postgresql_where=text(
                "execution_type = 'mission_campaign' and "
                "status in ('queued', 'running', 'pause_requested', 'paused', 'cancel_requested')"
            ),
            sqlite_where=text(
                "execution_type = 'mission_campaign' and "
                "status in ('queued', 'running', 'pause_requested', 'paused', 'cancel_requested')"
            ),
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
    team_plan_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_runs.id", ondelete="CASCADE"), nullable=True
    )
    execution_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_origin: Mapped[str] = mapped_column(String(32), default="legacy_pipeline", nullable=False)
    blueprint_version_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blueprint_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    frozen_execution_plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    execution_plan_schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_plan_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_plan_compiled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    clarification_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    clarification_schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    clarification_requests_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    clarification_decisions_json: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    resolved_execution_plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    resolved_execution_plan_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    clarification_model_slug_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    clarification_attempt_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clarification_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    clarification_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    clarification_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    clarification_provider_operation_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    clarification_provider_metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
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
    pause_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_stage_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    current_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latest_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_region: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_website: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_page: Mapped[str | None] = mapped_column(Text, nullable=True)
    regions_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    regions_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candidates_discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    websites_queued: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_visited: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pdfs_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_facilities: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    manual_review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicates_merged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    phones_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    emails_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    country_mismatches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    campaign_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_used: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    budget_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
        CheckConstraint("discovery_round >= 1", name="ck_source_discovery_query_discovery_round"),
        CheckConstraint("priority >= 0", name="ck_source_discovery_query_priority"),
        CheckConstraint(
            "generation_ordinal >= 0", name="ck_source_discovery_query_generation_ordinal"
        ),
        CheckConstraint("attempt_count >= 0", name="ck_source_discovery_query_attempt_count"),
        CheckConstraint(
            "lease_expires_at IS NULL OR claimed_at IS NULL OR lease_expires_at > claimed_at",
            name="ck_source_discovery_query_lease_after_claim",
        ),
        CheckConstraint("next_page_number >= 1", name="ck_source_discovery_query_next_page_number"),
        CheckConstraint("pages_completed >= 0", name="ck_source_discovery_query_pages_completed"),
        CheckConstraint(
            "last_page_result_count IS NULL OR last_page_result_count >= 0",
            name="ck_source_discovery_query_last_page_result_count",
        ),
        CheckConstraint(
            "last_page_fingerprint IS NULL OR length(trim(last_page_fingerprint)) = 64",
            name="ck_source_discovery_query_last_page_fingerprint_len",
        ),
        CheckConstraint(
            "("
            "(scope_level = 'countrywide' AND region_name IS NULL AND important_city IS NULL) OR "
            "(scope_level = 'region' AND region_name IS NOT NULL AND important_city IS NULL) OR "
            "(scope_level = 'city' AND region_name IS NOT NULL AND important_city IS NOT NULL)"
            ")",
            name="ck_source_discovery_query_scope_level",
        ),
        CheckConstraint(
            "("
            "(query_job_fingerprint IS NULL AND plan_hash_snapshot IS NULL) OR "
            "(query_job_fingerprint IS NOT NULL AND plan_hash_snapshot IS NOT NULL "
            "AND execution_id IS NOT NULL)"
            ")",
            name="ck_source_discovery_query_plan_backed_provenance",
        ),
        UniqueConstraint(
            "organization_id",
            "execution_id",
            "query_job_fingerprint",
            name="uq_source_discovery_query_fingerprint",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            "execution_id",
            name="uq_source_discovery_query_id_org_exec",
        ),
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
        Index(
            "ix_source_discovery_queries_round",
            "execution_id",
            "discovery_round",
            "priority",
            "generation_ordinal",
        ),
        Index(
            "ix_source_discovery_queries_pending_claim",
            "organization_id",
            "execution_id",
            "priority",
            "generation_ordinal",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index(
            "ix_source_discovery_queries_running_lease",
            "organization_id",
            "execution_id",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
            sqlite_where=text("status = 'running'"),
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
    region_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    language_code: Mapped[str] = mapped_column(String(16), nullable=False)
    language_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_category: Mapped[str] = mapped_column(String(120), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[SourceDiscoveryQueryStatus] = mapped_column(
        Enum(
            SourceDiscoveryQueryStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=SourceDiscoveryQueryStatus.PENDING,
        nullable=False,
    )
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    purpose: Mapped[str] = mapped_column(
        String(80), nullable=False, default="legacy_source_discovery"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    discovery_round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    generation_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    query_job_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_hash_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope_level: Mapped[str] = mapped_column(String(32), nullable=False, default="region")
    important_city: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # String(36) matches UUIDPrimaryKeyMixin / UuidFK — not native PG UUID.
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Safe machine-readable codes only — never raw provider payloads or PII.
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Restart-safe Serper page cursor (1-indexed). Not a campaign-wide page cap.
    next_page_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    pages_completed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    pagination_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_page_result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Technical loop-guard only — sha256_hex(payload_dict); never public.
    last_page_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pagination_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization: Mapped["Organization"] = relationship()
    execution: Mapped["ScrapingExecution | None"] = relationship()
    coverage_cell: Mapped["ScrapingCoverageCell | None"] = relationship()
    task: Mapped["ScrapingTask | None"] = relationship()
    candidates: Mapped[list["ScrapingSourceCandidate"]] = relationship(
        back_populates="discovery_query",
        cascade="all, delete-orphan",
        order_by="ScrapingSourceCandidate.rank",
    )


class ScrapingCrawlNode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Deduplicated crawl graph vertex for one campaign execution.

    Store ``canonical_url_hash`` via ``sha256_hex(payload_dict)`` of the
    canonical URL identity payload — never double-canonicalize before hashing.
    Mission ownership is via ``execution_id`` (same pattern as source candidates).
    """

    __tablename__ = "scraping_crawl_nodes"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "execution_id",
            "canonical_url_hash",
            name="uq_crawl_node_org_exec_url_hash",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            "execution_id",
            name="uq_crawl_node_id_org_exec",
        ),
        CheckConstraint(
            "source_classification IN ("
            "'official_facility_site', 'facility_profile', 'directory', 'registry', "
            "'government_source', 'commercial_listing', 'pdf', 'social_profile', "
            "'supporting_source', 'irrelevant', 'unclassified'"
            ")",
            name="ck_crawl_node_source_classification",
        ),
        CheckConstraint(
            "length(trim(canonical_url)) > 0",
            name="ck_crawl_node_canonical_url_not_blank",
        ),
        CheckConstraint(
            "length(trim(canonical_url_hash)) = 64",
            name="ck_crawl_node_canonical_url_hash_len",
        ),
        Index("ix_crawl_nodes_org_execution", "organization_id", "execution_id"),
        Index("ix_crawl_nodes_hostname", "hostname"),
        Index("ix_crawl_nodes_domain", "domain"),
        Index("ix_crawl_nodes_source_classification", "source_classification"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    source_classification: Mapped[CrawlNodeSourceClassification] = mapped_column(
        Enum(
            CrawlNodeSourceClassification,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        default=CrawlNodeSourceClassification.UNCLASSIFIED,
        nullable=False,
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    organization: Mapped["Organization"] = relationship(
        foreign_keys="[ScrapingCrawlNode.organization_id]",
    )
    execution: Mapped["ScrapingExecution"] = relationship(
        foreign_keys="[ScrapingCrawlNode.execution_id]",
    )
    # Composite FK (crawl_node_id, organization_id, execution_id) shares ownership
    # columns with Candidate.organization / .execution. Limit this relationship to
    # crawl_node_id so ORM sync does not fight those relationships; DB still enforces
    # org/execution isolation via fk_source_candidates_crawl_node_org_exec.
    source_candidates: Mapped[list["ScrapingSourceCandidate"]] = relationship(
        back_populates="crawl_node",
        primaryjoin=(
            "and_("
            "ScrapingCrawlNode.id == ScrapingSourceCandidate.crawl_node_id, "
            "ScrapingCrawlNode.organization_id == ScrapingSourceCandidate.organization_id, "
            "ScrapingCrawlNode.execution_id == ScrapingSourceCandidate.execution_id"
            ")"
        ),
        foreign_keys="[ScrapingSourceCandidate.crawl_node_id]",
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
        UniqueConstraint(
            "id",
            "organization_id",
            "execution_id",
            name="uq_source_candidate_id_org_exec",
        ),
        CheckConstraint("rank >= 1", name="ck_source_candidate_rank"),
        CheckConstraint(
            "provider_page_number IS NULL OR provider_page_number >= 1",
            name="ck_source_candidate_provider_page_number",
        ),
        CheckConstraint(
            "initial_relevance_score >= 0 AND initial_relevance_score <= 1",
            name="ck_source_candidate_relevance_score",
        ),
        CheckConstraint(
            "(lower(url) LIKE 'http://%' OR lower(url) LIKE 'https://%') AND "
            "(lower(canonical_url) LIKE 'http://%' OR lower(canonical_url) LIKE 'https://%')",
            name="ck_source_candidate_http_urls",
        ),
        CheckConstraint(
            "crawl_node_id IS NULL OR execution_id IS NOT NULL",
            name="ck_source_candidate_crawl_node_requires_execution",
        ),
        # Composite FK: RESTRICT (not SET NULL) — composite SET NULL would null
        # organization_id/execution_id. Clear crawl_node_id via UPDATE first.
        ForeignKeyConstraint(
            ["crawl_node_id", "organization_id", "execution_id"],
            [
                "scraping_crawl_nodes.id",
                "scraping_crawl_nodes.organization_id",
                "scraping_crawl_nodes.execution_id",
            ],
            name="fk_source_candidates_crawl_node_org_exec",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_source_candidates_org", "organization_id"),
        Index("ix_source_candidates_execution", "execution_id"),
        Index("ix_source_candidates_coverage", "coverage_cell_id"),
        Index("ix_source_candidates_query", "discovery_query_id"),
        Index("ix_source_candidates_provider", "provider"),
        Index("ix_source_candidates_domain", "domain"),
        Index("ix_source_candidates_status", "status"),
        Index("ix_source_candidates_canonical_url", "canonical_url"),
        Index("ix_source_candidates_crawl_node", "crawl_node_id"),
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
    crawl_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_result_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    # Within-page Serper provenance only — not an invented absolute cross-page rank.
    provider_page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    snippet: Mapped[str] = mapped_column(String(1000), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    country_name: Mapped[str] = mapped_column(String(120), nullable=False)
    region_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
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

    organization: Mapped["Organization"] = relationship(
        foreign_keys="[ScrapingSourceCandidate.organization_id]",
    )
    execution: Mapped["ScrapingExecution | None"] = relationship(
        foreign_keys="[ScrapingSourceCandidate.execution_id]",
    )
    coverage_cell: Mapped["ScrapingCoverageCell | None"] = relationship()
    discovery_query: Mapped["ScrapingSourceDiscoveryQuery"] = relationship(
        back_populates="candidates"
    )
    # Join includes org/execution equality for isolation; foreign_keys lists only
    # crawl_node_id so assignment writes the linkage column (not ownership columns).
    # Shared ownership columns with the composite FK are intentional; DB FK rejects
    # cross-org/execution crawl-node assignment.
    crawl_node: Mapped["ScrapingCrawlNode | None"] = relationship(
        back_populates="source_candidates",
        primaryjoin=(
            "and_("
            "ScrapingSourceCandidate.crawl_node_id == ScrapingCrawlNode.id, "
            "ScrapingSourceCandidate.organization_id == ScrapingCrawlNode.organization_id, "
            "ScrapingSourceCandidate.execution_id == ScrapingCrawlNode.execution_id"
            ")"
        ),
        foreign_keys="[ScrapingSourceCandidate.crawl_node_id]",
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


class ScrapingCrawlEdge(Base, UUIDPrimaryKeyMixin):
    """Directed crawl-graph edge within one org+execution.

    Composite FKs to ``(node.id, organization_id, execution_id)`` and provenance
    targets prevent cross-org/execution linkage at the database layer.
    """

    __tablename__ = "scraping_crawl_edges"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", "execution_id",
                         name="uq_crawl_edge_id_org_exec"),
        UniqueConstraint(
            "organization_id",
            "execution_id",
            "from_node_id",
            "to_node_id",
            "relationship_type",
            name="uq_crawl_edge_org_exec_rel",
        ),
        CheckConstraint("from_node_id <> to_node_id", name="ck_crawl_edge_no_self_loop"),
        CheckConstraint(
            "relationship_type IN ("
            "'directory_to_profile', 'profile_to_official_site', "
            "'directory_to_official_site', 'pagination', 'load_more', 'structured_api', "
            "'official_site_to_contact_page', 'official_site_to_program_page', "
            "'official_site_to_location_page', 'official_site_to_licensing_page', "
            "'official_site_to_evidence_page', 'related_source', 'discovered_link'"
            ")",
            name="ck_crawl_edge_relationship_type",
        ),
        ForeignKeyConstraint(
            ["from_node_id", "organization_id", "execution_id"],
            [
                "scraping_crawl_nodes.id",
                "scraping_crawl_nodes.organization_id",
                "scraping_crawl_nodes.execution_id",
            ],
            name="fk_crawl_edges_from_node_org_exec",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["to_node_id", "organization_id", "execution_id"],
            [
                "scraping_crawl_nodes.id",
                "scraping_crawl_nodes.organization_id",
                "scraping_crawl_nodes.execution_id",
            ],
            name="fk_crawl_edges_to_node_org_exec",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["discovery_query_id", "organization_id", "execution_id"],
            [
                "scraping_source_discovery_queries.id",
                "scraping_source_discovery_queries.organization_id",
                "scraping_source_discovery_queries.execution_id",
            ],
            name="fk_crawl_edges_discovery_query_org_exec",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["source_candidate_id", "organization_id", "execution_id"],
            [
                "scraping_source_candidates.id",
                "scraping_source_candidates.organization_id",
                "scraping_source_candidates.execution_id",
            ],
            name="fk_crawl_edges_source_candidate_org_exec",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_crawl_edges_org_execution", "organization_id", "execution_id"),
        Index("ix_crawl_edges_from_node", "from_node_id"),
        Index("ix_crawl_edges_to_node", "to_node_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False
    )
    from_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    to_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    relationship_type: Mapped[CrawlEdgeRelationshipType] = mapped_column(
        Enum(
            CrawlEdgeRelationshipType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
        ),
        nullable=False,
    )
    discovery_query_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship()
    execution: Mapped["ScrapingExecution"] = relationship()
    discovery_query: Mapped["ScrapingSourceDiscoveryQuery | None"] = relationship(
        primaryjoin=(
            "ScrapingCrawlEdge.discovery_query_id == ScrapingSourceDiscoveryQuery.id"
        ),
        foreign_keys="[ScrapingCrawlEdge.discovery_query_id]",
    )
    source_candidate: Mapped["ScrapingSourceCandidate | None"] = relationship(
        primaryjoin="ScrapingCrawlEdge.source_candidate_id == ScrapingSourceCandidate.id",
        foreign_keys="[ScrapingCrawlEdge.source_candidate_id]",
    )


class ScrapingPhase5WorkJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Incrementally created Phase 5 action; never a preloaded campaign backlog."""

    __tablename__ = "scraping_phase5_work_jobs"
    __table_args__ = (
        UniqueConstraint("organization_id", "execution_id", "fingerprint",
                         name="uq_phase5_job_org_exec_fingerprint"),
        UniqueConstraint("id", "organization_id", "execution_id",
                         name="uq_phase5_job_id_org_exec"),
        ForeignKeyConstraint(
            ["crawl_node_id", "organization_id", "execution_id"],
            ["scraping_crawl_nodes.id", "scraping_crawl_nodes.organization_id",
             "scraping_crawl_nodes.execution_id"],
            name="fk_phase5_job_node_org_exec", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_candidate_id", "organization_id", "execution_id"],
            ["scraping_source_candidates.id", "scraping_source_candidates.organization_id",
             "scraping_source_candidates.execution_id"],
            name="fk_phase5_job_candidate_org_exec", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["crawl_edge_id", "organization_id", "execution_id"],
            ["scraping_crawl_edges.id", "scraping_crawl_edges.organization_id",
             "scraping_crawl_edges.execution_id"],
            name="fk_phase5_job_edge_org_exec", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["discovery_query_id", "organization_id", "execution_id"],
            ["scraping_source_discovery_queries.id",
             "scraping_source_discovery_queries.organization_id",
             "scraping_source_discovery_queries.execution_id"],
            name="fk_phase5_job_query_org_exec", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["input_retrieval_result_id", "organization_id", "execution_id"],
            ["scraping_phase5_retrieval_results.id",
             "scraping_phase5_retrieval_results.organization_id",
             "scraping_phase5_retrieval_results.execution_id"],
            name="fk_phase5_job_input_retrieval_org_exec", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["input_source_document_id", "organization_id", "execution_id"],
            ["scraping_source_documents.id", "scraping_source_documents.organization_id",
             "scraping_source_documents.execution_id"],
            name="fk_phase5_job_input_document_org_exec", ondelete="RESTRICT",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_phase5_job_attempt_count"),
        CheckConstraint(
            "work_kind IN ('directory_expansion','http_retrieval',"
            "'firecrawl_retrieval','playwright_retrieval')",
            name="ck_phase5_job_work_kind",
        ),
        CheckConstraint(
            "status IN ('pending','running','succeeded','retry_scheduled',"
            "'blocked','rejected','failed','cancelled')",
            name="ck_phase5_job_status",
        ),
        CheckConstraint("max_attempts IS NULL OR max_attempts >= 1",
                        name="ck_phase5_job_max_attempts"),
        CheckConstraint("length(trim(fingerprint)) = 64",
                        name="ck_phase5_job_fingerprint_len"),
        CheckConstraint("lease_expires_at IS NULL OR claimed_at IS NULL OR "
                        "lease_expires_at > claimed_at",
                        name="ck_phase5_job_lease_after_claim"),
        CheckConstraint(
            "(work_kind = 'directory_expansion' AND selected_tool = 'directory_expansion') OR "
            "(work_kind = 'http_retrieval' AND selected_tool = 'http') OR "
            "(work_kind = 'firecrawl_retrieval' AND selected_tool = 'firecrawl') OR "
            "(work_kind = 'playwright_retrieval' AND selected_tool = 'playwright')",
            name="ck_phase5_job_kind_tool",
        ),
        CheckConstraint(
            "(status = 'running' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND claim_token IS NULL AND claimed_at IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_phase5_job_claim_state",
        ),
        CheckConstraint(
            "(canonical_url IS NULL AND status = 'rejected' AND "
            "last_error_category IS NOT NULL) OR canonical_url IS NOT NULL",
            name="ck_phase5_job_unsafe_terminal",
        ),
        CheckConstraint(
            "next_entry_ordinal >= 0 AND entries_completed >= 0 AND "
            "last_processed_slice_count >= 0",
            name="ck_phase5_job_expansion_cursor",
        ),
        CheckConstraint(
            "action_state_fingerprint IS NULL OR "
            "length(trim(action_state_fingerprint)) = 64",
            name="ck_phase5_job_action_state_fingerprint",
        ),
        CheckConstraint(
            "work_kind <> 'directory_expansion' OR "
            "(input_retrieval_result_id IS NOT NULL AND "
            "input_source_document_id IS NOT NULL AND "
            "input_content_fingerprint IS NOT NULL AND "
            "length(trim(input_content_fingerprint)) = 64 AND "
            "input_retrieval_method IN "
            "('http_retrieval','firecrawl_retrieval','playwright_retrieval'))",
            name="ck_phase5_job_directory_input",
        ),
        Index("ix_phase5_jobs_pending_claim", "organization_id", "execution_id",
              "status", "next_retry_at", "requested_at"),
        Index("ix_phase5_jobs_retry_schedule", "status", "next_retry_at"),
        Index("ix_phase5_jobs_running_lease", "status", "lease_expires_at"),
        Index("ix_phase5_jobs_input_retrieval", "input_retrieval_result_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False)
    source_candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    crawl_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    crawl_edge_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    discovery_query_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    input_retrieval_result_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    input_source_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    input_content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_retrieval_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action_state_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    work_kind: Mapped[Phase5WorkKind] = mapped_column(Enum(Phase5WorkKind, values_callable=lambda e: [x.value for x in e], native_enum=False), nullable=False)
    status: Mapped[Phase5WorkStatus] = mapped_column(Enum(Phase5WorkStatus, values_callable=lambda e: [x.value for x in e], native_enum=False), nullable=False, default=Phase5WorkStatus.PENDING)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_classification: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_tool: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_result_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    next_entry_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entries_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expansion_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_processed_slice_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expansion_parser_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    parser_state_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expansion_outcome: Mapped[str | None] = mapped_column(String(80), nullable=True)
    requires_managed_rendering: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_browser_interaction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    continuation_markers_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list,
        comment="Sanitized Phase 5B continuation markers; safe observed/canonical URLs only",
    )
    operational_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False,
        comment="Sanitized Phase 5 operational allowlist; never public or raw provider data",
    )


class ScrapingPhase5RetrievalResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_phase5_retrieval_results"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", "execution_id",
                         name="uq_phase5_retrieval_id_org_exec"),
        UniqueConstraint("organization_id", "execution_id", "work_job_id",
                         "result_fingerprint",
                         name="uq_phase5_retrieval_resource"),
        ForeignKeyConstraint(
            ["work_job_id", "organization_id", "execution_id"],
            ["scraping_phase5_work_jobs.id", "scraping_phase5_work_jobs.organization_id",
             "scraping_phase5_work_jobs.execution_id"],
            name="fk_phase5_retrieval_job_org_exec", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parent_crawl_edge_id", "organization_id", "execution_id"],
            ["scraping_crawl_edges.id", "scraping_crawl_edges.organization_id",
             "scraping_crawl_edges.execution_id"],
            name="fk_phase5_retrieval_edge_org_exec", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_document_id", "organization_id", "execution_id"],
            ["scraping_source_documents.id", "scraping_source_documents.organization_id",
             "scraping_source_documents.execution_id"],
            name="fk_phase5_retrieval_document_org_exec", ondelete="RESTRICT",
        ),
        CheckConstraint("redirect_count >= 0", name="ck_phase5_retrieval_redirect_count"),
        CheckConstraint("content_length IS NULL OR content_length >= 0",
                        name="ck_phase5_retrieval_content_length"),
        CheckConstraint(
            "retrieval_method IN ('http_retrieval','firecrawl_retrieval',"
            "'playwright_retrieval')",
            name="ck_phase5_retrieval_method",
        ),
        CheckConstraint("result_ordinal >= 0", name="ck_phase5_retrieval_result_ordinal"),
        CheckConstraint("length(trim(result_fingerprint)) = 64",
                        name="ck_phase5_retrieval_result_fingerprint_len"),
        Index("ix_phase5_retrieval_org_exec", "organization_id", "execution_id"),
        Index("ix_phase5_retrieval_response_fingerprint", "response_fingerprint"),
    )
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False)
    work_job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_role: Mapped[str] = mapped_column(String(64), nullable=False)
    result_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_method: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    redirect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_storage_reference: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    parent_crawl_edge_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_result_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    operational_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False,
        comment="Sanitized Phase 5 operational allowlist; never public or raw provider data",
    )


class ScrapingDirectoryObservation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Unverified listing observation; intentionally separate from facility candidates."""
    __tablename__ = "scraping_directory_observations"
    __table_args__ = (
        UniqueConstraint("organization_id", "execution_id", "observation_fingerprint",
                         name="uq_directory_observation_org_exec_fingerprint"),
        ForeignKeyConstraint(
            ["work_job_id", "organization_id", "execution_id"],
            ["scraping_phase5_work_jobs.id", "scraping_phase5_work_jobs.organization_id",
             "scraping_phase5_work_jobs.execution_id"],
            name="fk_directory_observation_job_org_exec", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parent_directory_node_id", "organization_id", "execution_id"],
            ["scraping_crawl_nodes.id", "scraping_crawl_nodes.organization_id",
             "scraping_crawl_nodes.execution_id"],
            name="fk_directory_observation_parent_node_org_exec", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["emitted_profile_node_id", "organization_id", "execution_id"],
            ["scraping_crawl_nodes.id", "scraping_crawl_nodes.organization_id",
             "scraping_crawl_nodes.execution_id"],
            name="fk_directory_observation_profile_node_org_exec",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["emitted_website_node_id", "organization_id", "execution_id"],
            ["scraping_crawl_nodes.id", "scraping_crawl_nodes.organization_id",
             "scraping_crawl_nodes.execution_id"],
            name="fk_directory_observation_website_node_org_exec",
            ondelete="RESTRICT",
        ),
        CheckConstraint("listing_rank IS NULL OR listing_rank >= 1",
                        name="ck_directory_observation_rank"),
        CheckConstraint("length(trim(observation_fingerprint)) = 64",
                        name="ck_directory_observation_fingerprint_len"),
        Index("ix_directory_observations_org_exec", "organization_id", "execution_id"),
        Index("ix_directory_observations_parent", "parent_directory_node_id"),
    )
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False)
    work_job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    observation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    displayed_facility_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    listing_page_url: Mapped[str] = mapped_column(Text, nullable=False)
    profile_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    displayed_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    displayed_phone: Mapped[str | None] = mapped_column(String(120), nullable=True)
    displayed_region: Mapped[str | None] = mapped_column(String(160), nullable=True)
    displayed_city: Mapped[str | None] = mapped_column(String(160), nullable=True)
    directory_source: Mapped[str] = mapped_column(String(255), nullable=False)
    listing_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_payload_reference: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    parent_directory_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    emitted_profile_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    emitted_website_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
        UniqueConstraint("id", "organization_id", "execution_id",
                         name="uq_source_document_id_org_exec"),
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
        UniqueConstraint("id", "organization_id", "execution_id", name="uq_source_document_chunk_owner"),
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
    retrieval_result_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_phase5_retrieval_results.id", ondelete="SET NULL"), nullable=True
    )
    crawl_node_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_crawl_nodes.id", ondelete="SET NULL"), nullable=True
    )
    original_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    representation_provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
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
        UniqueConstraint("id", "organization_id", "execution_id", name="uq_facility_candidate_owner"),
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
    directory_observation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_directory_observations.id", ondelete="SET NULL"), nullable=True
    )


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
    retrieval_result_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_phase5_retrieval_results.id", ondelete="SET NULL"), nullable=True
    )
    crawl_node_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scraping_crawl_nodes.id", ondelete="SET NULL"), nullable=True
    )
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
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


class ScrapingFacilityPhaseWorkJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_facility_phase_work_jobs"
    __table_args__ = (
        UniqueConstraint("organization_id", "execution_id", "fingerprint", name="uq_facility_phase_job_fingerprint"),
        UniqueConstraint("id", "organization_id", "execution_id", name="uq_facility_phase_job_owner"),
        CheckConstraint("length(fingerprint) = 64", name="ck_facility_phase_job_fingerprint"),
        CheckConstraint("attempt_count >= 0", name="ck_facility_phase_job_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_facility_phase_job_max_attempts"),
        CheckConstraint("work_kind IN ('prepare_document','extract_chunk','verify_candidate','deduplicate_candidate',"
                        "'publish_candidate','generate_execution_export','finalize_execution')",
                        name="ck_facility_phase_job_kind"),
        CheckConstraint("status IN ('pending','running','retry_scheduled','succeeded','failed','cancelled')",
                        name="ck_facility_phase_job_status"),
        Index("ix_facility_phase_jobs_claim", "organization_id", "execution_id", "status", "next_retry_at"),
        Index("ix_facility_phase_jobs_lease", "status", "lease_expires_at"),
    )
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False)
    work_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_document_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scraping_source_documents.id", ondelete="CASCADE"))
    chunk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scraping_source_document_chunks.id", ondelete="CASCADE"))
    facility_candidate_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scraping_facility_candidates.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    claim_token: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_classification: Mapped[str | None] = mapped_column(String(80))
    safe_error_message: Mapped[str | None] = mapped_column(String(500))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ScrapingExecutionExport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_execution_exports"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "execution_id", "export_kind",
            name="uq_scraping_execution_export_kind",
        ),
        UniqueConstraint(
            "id", "organization_id", "execution_id",
            name="uq_scraping_execution_export_owner",
        ),
        CheckConstraint(
            "status IN ('pending','succeeded','failed')",
            name="ck_scraping_execution_export_status",
        ),
        CheckConstraint(
            "status != 'succeeded' OR "
            "(artifact_sha256 IS NOT NULL AND artifact_bytes IS NOT NULL "
            "AND filename IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_scraping_execution_export_succeeded",
        ),
        Index(
            "ix_scraping_execution_exports_execution",
            "organization_id", "execution_id", "status",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    export_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="xlsx"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending"
    )
    filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(120))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    failure_classification: Mapped[str | None] = mapped_column(String(80))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class ScrapingFacilityCandidateDecision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_facility_candidate_decisions"
    __table_args__ = (
        UniqueConstraint("organization_id", "execution_id", "facility_candidate_id",
                         name="uq_facility_candidate_decision"),
        Index("ix_facility_candidate_identity", "organization_id", "execution_id", "identity_fingerprint"),
        CheckConstraint("country_decision IN ('inside_requested_country','outside_requested_country','uncertain')",
                        name="ck_facility_candidate_country_decision"),
        CheckConstraint("final_status IN ('accepted','needs_review','rejected')",
                        name="ck_facility_candidate_final_status"),
        Index("ix_facility_candidate_decisions_status", "organization_id", "execution_id", "final_status"),
    )
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False)
    facility_candidate_id: Mapped[str] = mapped_column(String(36), ForeignKey("scraping_facility_candidates.id", ondelete="CASCADE"), nullable=False)
    canonical_candidate_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scraping_facility_candidates.id", ondelete="SET NULL"))
    requested_country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    country_decision: Mapped[str] = mapped_column(String(40), nullable=False)
    country_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    country_evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    identity_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    final_status: Mapped[str] = mapped_column(String(24), nullable=False)
    final_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)


class ScrapingFacilityCandidateDuplicate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scraping_facility_candidate_duplicates"
    __table_args__ = (
        UniqueConstraint("organization_id", "execution_id", "left_candidate_id", "right_candidate_id",
                         name="uq_facility_candidate_duplicate_pair"),
        CheckConstraint("left_candidate_id < right_candidate_id", name="ck_facility_candidate_duplicate_order"),
        CheckConstraint("relationship IN ('probable_duplicate','distinct_branch')",
                        name="ck_facility_candidate_duplicate_relationship"),
        Index("ix_facility_candidate_duplicates_execution", "organization_id", "execution_id"),
    )
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), ForeignKey("scraping_executions.id", ondelete="CASCADE"), nullable=False)
    left_candidate_id: Mapped[str] = mapped_column(String(36), ForeignKey("scraping_facility_candidates.id", ondelete="CASCADE"), nullable=False)
    right_candidate_id: Mapped[str] = mapped_column(String(36), ForeignKey("scraping_facility_candidates.id", ondelete="CASCADE"), nullable=False)
    relationship: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)


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
        Enum(
            TurnStatus,
            native_enum=False,
        ),
        default=TurnStatus.PENDING,
        nullable=False,
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


class ContentLabel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "content_labels"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "name", name="uq_content_label_org_user_name"),
        Index("ix_content_labels_org_user", "org_id", "user_id"),
    )

    org_id: Mapped[str] = UuidFK("organizations")
    user_id: Mapped[str] = UuidFK("users")
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    documents: Mapped[list["SavedDocument"]] = relationship(
        secondary="saved_document_labels",
        back_populates="labels",
    )


class SavedDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """User-managed snapshot of a full chat turn (not a Lesson)."""

    __tablename__ = "saved_documents"
    __table_args__ = (
        Index("ix_saved_documents_org_user_updated", "org_id", "user_id", "updated_at"),
        Index("ix_saved_documents_turn_id", "turn_id"),
    )

    org_id: Mapped[str] = UuidFK("organizations")
    user_id: Mapped[str] = UuidFK("users")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chats.id", ondelete="SET NULL"), nullable=True
    )
    turn_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("turns.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    chat_title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    labels: Mapped[list["ContentLabel"]] = relationship(
        secondary="saved_document_labels",
        back_populates="documents",
    )


class SavedDocumentLabel(Base):
    __tablename__ = "saved_document_labels"
    __table_args__ = (
        UniqueConstraint("document_id", "label_id", name="uq_saved_document_label"),
    )

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("saved_documents.id", ondelete="CASCADE"), primary_key=True
    )
    label_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("content_labels.id", ondelete="CASCADE"), primary_key=True
    )


class BrainKnowledgeItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Permissioned knowledge chunk for hybrid Brain retrieval."""

    __tablename__ = "brain_knowledge_items"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "user_id",
            "source_type",
            "source_id",
            name="uq_brain_knowledge_source",
        ),
        Index("ix_brain_knowledge_org_user", "org_id", "user_id"),
        Index("ix_brain_knowledge_project", "project_id"),
        Index("ix_brain_knowledge_source_type", "source_type"),
    )

    org_id: Mapped[str] = UuidFK("organizations")
    user_id: Mapped[str] = UuidFK("users")
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)


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
