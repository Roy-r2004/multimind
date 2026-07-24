"""Pydantic request/response schemas — shared contract with frontend."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class StrategyEnum(str, Enum):
    RECONCILE = "Reconcile"
    SYNTHESIZE = "Synthesize"
    RANK = "Rank"
    PICK_BEST = "Pick Best"
    DEBATE = "Debate"


# --- Auth ---


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=255)
    org_name: str = Field(default="My Organization", max_length=255)


class SignInRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    avatar_url: str | None = None


class OrgResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    plan: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse | None = None
    organization: OrgResponse | None = None


class SessionResponse(BaseModel):
    user: UserResponse
    organization: OrgResponse


# --- Models catalog ---


class ModelPricingResponse(BaseModel):
    input_per_1k: float
    output_per_1k: float
    source: str
    openrouter_slug: str | None = None


class ModelResponse(BaseModel):
    id: str
    name: str
    vendor: str
    color: str
    blurb: str
    is_custom: bool = False
    openrouter_slug: str | None = None
    pricing: ModelPricingResponse | None = None


class ModelSearchResult(BaseModel):
    openrouter_slug: str
    name: str
    description: str = ""
    vendor: str
    context_length: int | None = None
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0


class ModelAddRequest(BaseModel):
    openrouter_slug: str = Field(min_length=3, max_length=256)


# --- Model Sets ---


class ModelSetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    models: list[str]
    verdict_model: str
    strategy: StrategyEnum
    best_for: str
    template_name: str | None = None
    custom_instructions: str | None = None
    is_system: bool = False


class ModelSetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    models: list[str] = Field(min_length=1, max_length=5)
    verdict_model: str
    strategy: StrategyEnum = StrategyEnum.SYNTHESIZE
    best_for: str = ""
    template_name: str | None = None
    custom_instructions: str | None = None


class ModelSetUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    models: list[str] | None = Field(default=None, min_length=1, max_length=5)
    verdict_model: str | None = None
    strategy: StrategyEnum | None = None
    best_for: str | None = None
    template_name: str | None = None
    custom_instructions: str | None = None


# --- Projects ---


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    chat_count: int = 0
    updated_at: datetime


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class ProjectScrapingMissionResponse(BaseModel):
    id: str
    title: str
    status: str
    project_id: str | None = None
    country_code: str | None = None
    country_name: str | None = None
    active_blueprint_id: str | None = None
    created_at: datetime
    updated_at: datetime


# --- Scraping Council ---


class ScrapingMissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    country_code: str
    original_prompt: str
    model_set_id: str
    project_id: str | None = None


class ScrapingMissionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=512)
    country_code: str | None = None
    project_id: str | None = None


class ScrapingBlueprintGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pass


class ScrapingBlueprintApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pass


class ScrapingBlueprintRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class ScrapingBlueprintChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_instructions: str


class ScrapingBlueprintRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Blueprint name is required")
        return value


class ScrapingMissionSummary(BaseModel):
    id: str
    title: str
    original_prompt: str
    status: str
    country_code: str | None = None
    country_name: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    active_blueprint_id: str | None = None
    active_blueprint_version: int | None = None
    created_at: datetime
    updated_at: datetime


class ScrapingMissionDetail(ScrapingMissionSummary):
    created_by: str
    project_id: str | None = None
    project_name: str | None = None
    model_set_id: str
    model_set_name: str | None = None


class BlueprintMissionSummary(BaseModel):
    goal: str
    target_entities: list[str]
    deliverables: list[str]

    @field_validator("goal")
    @classmethod
    def goal_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("mission goal cannot be empty")
        return value

    @field_validator("target_entities")
    @classmethod
    def target_entities_required(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("target_entities cannot be empty")
        return value


class BlueprintScope(BaseModel):
    included: list[str]
    excluded: list[str]
    countries: list[str]
    regions: list[str]


class BlueprintSearchTerm(BaseModel):
    language: str
    term: str
    purpose: str


class BlueprintSourceStrategyItem(BaseModel):
    source_type: str
    priority: int = Field(ge=1)
    trust_tier: str
    purpose: str
    required: bool


class BlueprintDataSchemaItem(BaseModel):
    field_name: str
    description: str
    required: bool


class BlueprintTaskPlanItem(BaseModel):
    order: int = Field(ge=1)
    task: str
    assigned_role: str


class BlueprintEstimatedWorkload(BaseModel):
    expected_queries: int | None = Field(default=None, ge=0)
    expected_pages: int | None = Field(default=None, ge=0)
    expected_ai_calls: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    notes: list[str]


class BlueprintAgentAssignment(BaseModel):
    role: str
    responsibility: str
    model_id: str


class ScrapingBlueprintContent(BaseModel):
    mission_summary: BlueprintMissionSummary
    scope: BlueprintScope
    languages: list[str]
    search_terms: list[BlueprintSearchTerm]
    source_strategy: list[BlueprintSourceStrategyItem]
    data_schema: list[BlueprintDataSchemaItem]
    classification_rules: list[str]
    verification_rules: list[str]
    deduplication_rules: list[str]
    compliance_rules: list[str]
    task_plan: list[BlueprintTaskPlanItem]
    stop_conditions: list[str]
    estimated_workload: BlueprintEstimatedWorkload
    agent_assignments: list[BlueprintAgentAssignment]

    @field_validator("task_plan")
    @classmethod
    def task_plan_required(cls, value: list[BlueprintTaskPlanItem]) -> list[BlueprintTaskPlanItem]:
        if not value:
            raise ValueError("task_plan cannot be empty")
        return value


class ScrapingBlueprintResponse(BaseModel):
    id: str
    mission_id: str
    version: int
    display_name: str | None = None
    status: str
    blueprint_json: ScrapingBlueprintContent | None = None
    model_set_id: str
    judge_model_id: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    change_instructions: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ScrapingRunAgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    assigned_scope: dict[str, Any] = Field(default_factory=dict)
    model_id: str = Field(min_length=1, max_length=64)
    depends_on: list[int] = Field(default_factory=list)

    @field_validator("name", "role", "purpose", "instructions", "model_id", mode="before")
    @classmethod
    def trim_required_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        return value


class ScrapingTeamPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommended_agent_count: int
    rationale: str = Field(min_length=1)
    agents: list[ScrapingRunAgentPlan] = Field(min_length=1)

    @field_validator("rationale", mode="before")
    @classmethod
    def trim_rationale(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        return value

    @model_validator(mode="after")
    def validate_agent_count(self) -> "ScrapingTeamPlanOutput":
        if self.recommended_agent_count != len(self.agents):
            raise ValueError("recommended_agent_count must equal the number of agents")
        return self


class ScrapingRunAgentResponse(BaseModel):
    id: str
    run_id: str
    parent_agent_id: str | None = None
    sequence: int
    name: str
    role: str
    purpose: str
    instructions: str
    assigned_scope: dict[str, Any]
    model_id: str
    status: str
    dependency_agent_ids: list[str]
    created_at: datetime
    updated_at: datetime


class ScrapingRunSummary(BaseModel):
    id: str
    mission_id: str
    blueprint_id: str
    blueprint_version: int | None = None
    status: str
    recommended_agent_count: int | None = None
    planner_model_id: str | None = None
    planner_rationale: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScrapingRunDetail(ScrapingRunSummary):
    model_set_id: str
    mission_title: str
    plan_json: ScrapingTeamPlanOutput | None = None
    agents: list[ScrapingRunAgentResponse] = []


class ScrapingExecutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_type: str = "initial_full_country"
    # real = standard throughput; full_census = high-limit country census run
    mode: str = "real"


class ScrapingExecutionAgentResponse(BaseModel):
    id: str
    execution_id: str
    team_agent_id: str
    planned_agent_name: str
    planned_agent_role: str
    model_id: str
    status: str
    current_task_id: str | None = None
    current_task_title: str | None = None
    current_action: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ScrapingExecutionSummary(BaseModel):
    id: str
    organization_id: str
    mission_id: str
    blueprint_id: str
    team_plan_id: str
    execution_type: str
    mode: str
    status: str
    status_label: str
    country_code: str
    country_name: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    heartbeat_at: datetime | None = None
    error_message: str | None = None
    sources_discovered: int
    documents_found: int
    records_extracted: int
    records_verified: int
    duplicates_detected: int
    blocked_sources: int
    coverage_debt: int
    created_at: datetime
    updated_at: datetime


class ScrapingFacilitySummary(BaseModel):
    id: str
    execution_id: str
    stable_key: str
    canonical_name: str
    country_code: str
    country_name: str
    primary_region: str | None = None
    primary_city: str | None = None
    facility_type: str
    primary_website: str | None = None
    primary_contact: str | None = None
    verification_status: str
    confidence_score: float
    human_review_status: str
    is_mock: bool
    source_count: int
    location_count: int = 0
    contact_count: int = 0
    treatment_service_count: int = 0
    created_at: datetime
    updated_at: datetime


class ScrapingFacilityAliasItem(BaseModel):
    name: str
    alias_type: str
    is_primary: bool


class ScrapingFacilityLocationItem(BaseModel):
    id: str
    location_type: str
    location_name: str
    full_address: str | None = None
    city: str | None = None
    region: str | None = None
    is_primary: bool
    confidence_score: float


class ScrapingFacilityContactItem(BaseModel):
    id: str
    contact_type: str
    label: str | None = None
    value: str
    is_primary: bool
    confidence_score: float


class ScrapingFacilityAttributeItem(BaseModel):
    id: str
    attribute_group: str
    attribute_key: str
    display_name: str
    value_text: str | None = None
    confidence_score: float


class ScrapingFacilitySourceItem(BaseModel):
    id: str
    url: str
    title: str | None = None
    relationship_type: str


class ScrapingFacilityEvidenceItem(BaseModel):
    id: str
    field_path: str
    extracted_value: str | None = None
    evidence_text: str | None = None
    source_url_snapshot: str | None = None
    page_title: str | None = None


class ScrapingFacilityDetail(ScrapingFacilitySummary):
    description: str | None = None
    primary_address: str | None = None
    aliases: list[ScrapingFacilityAliasItem] = Field(default_factory=list)
    locations: list[ScrapingFacilityLocationItem] = Field(default_factory=list)
    contacts: list[ScrapingFacilityContactItem] = Field(default_factory=list)
    attributes: list[ScrapingFacilityAttributeItem] = Field(default_factory=list)
    sources: list[ScrapingFacilitySourceItem] = Field(default_factory=list)
    evidence: list[ScrapingFacilityEvidenceItem] = Field(default_factory=list)


class ScrapingCoverageCellResponse(BaseModel):
    id: str
    execution_id: str
    region_code: str | None = None
    region_name: str
    language_code: str | None = None
    language_name: str
    source_category: str
    status: str
    assigned_execution_agent_id: str | None = None
    assigned_agent_name: str | None = None
    result_count: int
    reason: str | None = None
    metadata_json: dict[str, Any]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScrapingTaskResponse(BaseModel):
    id: str
    execution_id: str
    execution_agent_id: str
    agent_name: str | None = None
    coverage_cell_id: str | None = None
    coverage_label: str | None = None
    parent_task_id: str | None = None
    task_type: str
    title: str
    status: str
    priority: int
    attempt_count: int
    max_attempts: int
    current_action: str | None = None
    input_json: dict[str, Any]
    output_json: dict[str, Any]
    dependency_task_ids_json: list[str]
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ScrapingEventResponse(BaseModel):
    id: str
    execution_id: str
    execution_agent_id: str | None = None
    task_id: str | None = None
    coverage_cell_id: str | None = None
    sequence_number: int
    event_type: str
    message: str
    metadata_json: dict[str, Any]
    created_at: datetime


class ScrapingExecutionDetail(BaseModel):
    execution: ScrapingExecutionSummary
    country_profile: dict[str, Any] | None = None
    agents: list[ScrapingExecutionAgentResponse]
    task_summary_counts: dict[str, int]
    coverage_summary_counts: dict[str, int]
    recent_tasks: list[ScrapingTaskResponse]
    recent_events: list[ScrapingEventResponse]
    can_cancel: bool
    can_delete: bool
    mock: bool = False


class SourceDiscoveryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    execution_id: str | None = None
    coverage_cell_id: str | None = None
    task_id: str | None = None
    country_code: str = Field(min_length=2, max_length=2)
    country_name: str = Field(min_length=1, max_length=120)
    region_code: str | None = Field(default=None, max_length=32)
    region_name: str = Field(min_length=1, max_length=160)
    language_code: str = Field(min_length=1, max_length=16)
    language_name: str = Field(min_length=1, max_length=120)
    source_category: str = Field(min_length=1, max_length=120)
    mission_goal: str = Field(min_length=1, max_length=2000)
    requested_fields: list[str] = Field(default_factory=list, max_length=50)
    blueprint_context: dict[str, Any] = Field(default_factory=dict)
    provider: str = Field(default="serper", min_length=1, max_length=64)
    max_queries_per_discovery: int | None = Field(default=None, ge=1, le=32)
    results_per_query: int | None = Field(default=None, ge=1, le=100)
    discovery_query_hard_cap: int | None = Field(default=None, ge=1, le=32)
    discovery_results_hard_cap: int | None = Field(default=None, ge=1, le=100)

    @field_validator("requested_fields")
    @classmethod
    def bound_requested_fields(cls, value: list[str]) -> list[str]:
        return [item.strip()[:120] for item in value if item.strip()]


class SourceDiscoveryPlannedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=240)
    language_code: str = Field(min_length=1, max_length=16)
    purpose: str = Field(min_length=1, max_length=300)

    @field_validator("query", "language_code", "purpose", mode="before")
    @classmethod
    def trim_required_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class SourceDiscoveryQueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Keep in sync with SourceDiscoveryContext.discovery_query_hard_cap (le=32).
    queries: list[SourceDiscoveryPlannedQuery] = Field(min_length=1, max_length=32)


class SourceDiscoveryProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_result_id: str | None = Field(default=None, max_length=255)
    rank: int = Field(ge=1)
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="", max_length=300)
    snippet: str = Field(default="", max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceDiscoveryQueryResponse(BaseModel):
    id: str
    organization_id: str
    execution_id: str | None = None
    coverage_cell_id: str | None = None
    task_id: str | None = None
    country_code: str
    country_name: str
    region_code: str | None = None
    region_name: str
    language_code: str
    language_name: str
    source_category: str
    query_text: str
    provider: str
    status: str
    requested_at: datetime
    completed_at: datetime | None = None
    result_count: int
    error_code: str | None = None
    error_message: str | None = None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SourceCandidateResponse(BaseModel):
    id: str
    organization_id: str
    execution_id: str | None = None
    coverage_cell_id: str | None = None
    discovery_query_id: str
    provider: str
    provider_result_id: str | None = None
    rank: int
    url: str
    canonical_url: str
    domain: str
    title: str
    snippet: str
    country_code: str
    country_name: str
    region_code: str | None = None
    region_name: str
    language_code: str
    language_name: str
    source_category: str
    initial_relevance_score: float
    initial_trust_tier: str
    status: str
    discovered_at: datetime
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SourceDiscoverySummary(BaseModel):
    provider: str
    planned_query_count: int
    query_count: int
    succeeded_query_count: int
    failed_query_count: int
    candidate_count: int
    duplicate_candidate_count: int
    rejected_result_count: int


class SourceRetrievalAttemptResponse(BaseModel):
    id: str
    organization_id: str
    execution_id: str
    source_candidate_id: str
    coverage_cell_id: str | None = None
    task_id: str | None = None
    status: str
    requested_url: str
    final_url: str | None = None
    redirect_count: int
    http_status: int | None = None
    content_type: str | None = None
    declared_content_length: int | None = None
    bytes_received: int | None = None
    robots_status: str | None = None
    failure_classification: str | None = None
    safe_error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SourceDocumentResponse(BaseModel):
    id: str
    organization_id: str
    execution_id: str
    source_candidate_id: str
    retrieval_attempt_id: str
    final_url: str
    content_type: str
    charset: str | None = None
    content_sha256: str
    byte_size: int
    retrieval_timestamp: datetime
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class PreparedSourceTextAuditResponse(BaseModel):
    id: str
    source_document_id: str
    source_candidate_id: str | None = None
    coverage_cell_id: str | None = None
    parser_version: str
    title: str | None = None
    status: str
    failure_classification: str | None = None
    character_count: int
    original_character_count: int
    truncated: bool
    prepared_text_hash_prefix: str
    created_at: datetime
    updated_at: datetime


class SourceDocumentChunkAuditResponse(BaseModel):
    id: str
    source_document_id: str
    prepared_text_id: str
    coverage_cell_id: str | None = None
    chunk_index: int
    character_start: int
    character_end: int
    character_count: int
    chunk_hash_prefix: str
    preview: str
    created_at: datetime


class FacilityExtractionAttemptAuditResponse(BaseModel):
    id: str
    source_document_id: str
    prepared_text_id: str
    chunk_id: str
    coverage_cell_id: str | None = None
    provider: str
    model: str
    prompt_version: str
    status: str
    attempt_number: int
    requested_at: datetime
    completed_at: datetime | None = None
    input_character_count: int
    output_candidate_count: int
    failure_classification: str | None = None
    safe_error_message: str | None = None


class FacilityCandidateAuditResponse(BaseModel):
    id: str
    coverage_cell_id: str | None = None
    source_document_id: str
    prepared_text_id: str
    chunk_id: str
    extraction_attempt_id: str
    raw_name: str
    model_confidence: float | None = None
    staging_status: str
    candidate_fingerprint_prefix: str
    created_at: datetime
    updated_at: datetime


class FacilityCandidateEvidenceAuditResponse(BaseModel):
    id: str
    facility_candidate_id: str
    source_document_id: str
    prepared_text_id: str
    chunk_id: str
    field_name: str
    raw_value_preview: str | None = None
    evidence_quote: str
    quote_start: int
    quote_end: int
    evidence_hash_prefix: str
    verification_status: str
    created_at: datetime


class FacilityCandidatePublicationAuditResponse(BaseModel):
    id: str
    facility_candidate_id: str
    final_facility_id: str | None = None
    status: str
    reason_code: str | None = None
    normalization_version: str
    metadata_json: dict[str, Any]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# --- Chats ---


class ChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    project_id: str | None = None
    pinned_verdict_id: str | None = None
    pinned_turn_id: str | None = None
    updated_at: datetime


class ProjectDetailResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    chat_count: int = 0
    updated_at: datetime
    chats: list[ChatResponse] = []
    scraping_missions: list[ProjectScrapingMissionResponse] = []


class ChatCreateRequest(BaseModel):
    title: str = "New chat"
    project_id: str | None = None


class ChatUpdateRequest(BaseModel):
    title: str | None = None
    project_id: str | None = None


class PinVerdictRequest(BaseModel):
    verdict_id: str


# --- Turns ---


class ModelAnswerResponse(BaseModel):
    model_id: str
    model_name: str
    text: str | None = None
    confidence: int | None = None
    status: str
    error_message: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0


class VerdictResponse(BaseModel):
    id: str
    model_id: str
    strategy: StrategyEnum
    text: str
    reason: str
    saved: bool = False
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0


class SavedVerdictSaveResponse(BaseModel):
    verdict_id: str
    saved: bool = True
    id: str
    source_verdict_id: str
    source_turn_id: str | None = None
    source_chat_id: str | None = None
    source_chat_title: str
    source_user_message: str
    verdict_text: str
    verdict_reason: str
    verdict_model_id: str
    strategy: StrategyEnum
    saved_at: datetime
    original_chat_exists: bool = True
    original_chat_route: str | None = None


class SavedVerdictUnsaveResponse(BaseModel):
    verdict_id: str
    saved: bool = False


class SavedVerdictDeleteResponse(BaseModel):
    id: str
    deleted: bool = True


class SavedVerdictPurgeResponse(BaseModel):
    deleted_count: int


class SavedVerdictListItemResponse(BaseModel):
    id: str
    source_verdict_id: str
    source_turn_id: str | None = None
    source_chat_id: str | None = None
    source_chat_title: str
    source_user_message: str
    verdict_text: str
    verdict_reason: str
    verdict_model_id: str
    strategy: StrategyEnum
    saved_at: datetime
    original_chat_exists: bool
    original_chat_route: str | None = None


class DecisionInsuranceResponse(BaseModel):
    best_case: str
    worst_case: str
    risk_level: str
    potential_loss: str
    mitigation_plan: str
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0


class TurnCreateRequest(BaseModel):
    user_message: str = Field(min_length=1)
    model_set_id: str
    decision_insurance_enabled: bool = False
    custom_instructions: str | None = None


class PromptBuilderImproveRequest(BaseModel):
    raw_prompt: str | None = Field(default=None, max_length=4000)


class PromptBuilderImproveResponse(BaseModel):
    improved_prompt: str


class TurnResponse(BaseModel):
    id: str
    chat_id: str
    user_message: str
    model_set_id: str
    strategy: StrategyEnum
    verdict_model: str
    status: str
    model_answers: list[ModelAnswerResponse] = []
    verdict: VerdictResponse | None = None
    decision_insurance: DecisionInsuranceResponse | None = None
    lesson_id: str | None = None
    lesson_status: str | None = None
    created_at: datetime


class TurnDeleteResponse(BaseModel):
    turn_id: str
    deleted: bool


class VerdictDisagreeRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=8000)
    user_position: str = Field(min_length=10, max_length=8000)


class DiscussMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class DiscussMessageItem(BaseModel):
    role: str
    content: str
    kind: str | None = None
    model_id: str | None = None
    model_name: str | None = None
    confidence: int | None = None
    turn_id: str | None = None


class DiscussResponse(BaseModel):
    lesson_id: str
    messages: list[DiscussMessageItem]
    can_finalize: bool = False


class LessonAgreementItem(BaseModel):
    topic: str
    detail: str


class LessonDisagreementItem(BaseModel):
    topic: str
    user_view: str
    model_view: str
    analysis: str


class LessonEvidenceItem(BaseModel):
    claim: str
    user_evidence: str
    model_evidence: str
    assessment: str


class LessonTakeaway(BaseModel):
    headline: str = ""
    key_insight: str = ""
    what_to_remember: list[str] = []
    when_user_might_be_right: str = ""
    when_model_might_be_right: str = ""
    recommended_next_step: str = ""


class LessonComparisonResponse(BaseModel):
    overview: str = ""
    user_position_summary: str = ""
    model_position_summary: str = ""
    agreements: list[LessonAgreementItem] = []
    disagreements: list[LessonDisagreementItem] = []
    evidence: list[LessonEvidenceItem] = []
    assumptions: dict[str, list[str]] = Field(default_factory=lambda: {"user": [], "model": []})
    blind_spots: dict[str, list[str]] = Field(default_factory=lambda: {"user": [], "model": []})
    lesson: LessonTakeaway = Field(default_factory=LessonTakeaway)
    facilitator_stance: str | None = None
    outcome: str | None = None
    outcome_summary: str | None = None


class LessonListItemResponse(BaseModel):
    id: str
    turn_id: str | None = None
    chat_id: str | None = None
    title: str
    summary: str
    user_name: str
    verdict_model_name: str
    status: str
    facilitator_stance: str | None = None
    created_at: datetime


class LessonDetailResponse(LessonListItemResponse):
    user_message: str
    disagreement_reason: str
    user_position: str
    verdict_model_id: str
    verdict_text: str
    verdict_reason: str
    strategy: StrategyEnum
    comparison: LessonComparisonResponse
    discussion_messages: list[DiscussMessageItem] = []
    outcome: str | None = None
    outcome_summary: str | None = None
    error_message: str | None = None


class DiscussFinalizeResponse(BaseModel):
    lesson: LessonDetailResponse


# --- Templates ---


class TemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    category: str
    instructions: str
    is_system: bool = False


class TemplateCreateRequest(BaseModel):
    title: str
    description: str = ""
    category: str
    instructions: str


# --- Costs ---


class CostSummaryResponse(BaseModel):
    today_usd: float
    week_usd: float
    month_usd: float
    month_tokens: int
    budget_usd: float
    budget_used_pct: float
    by_model: list[dict]


class AdminOverviewResponse(BaseModel):
    organization_id: str
    organization_name: str
    organization_slug: str
    plan: str
    user_role: str
    total_members: int
    total_projects: int
    total_chats: int
    total_model_sets: int
    total_templates: int
    monthly_budget_usd: float


class AdminMemberResponse(BaseModel):
    id: str
    membership_id: str
    user_id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    joined_at: datetime


class AdminCreateMemberRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    role: str
    temporary_password: str = Field(min_length=1)


class AdminUpdateMemberRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class AdminMemberActionResponse(BaseModel):
    message: str


class AdminUsageResponse(CostSummaryResponse):
    total_turns: int
    total_cost_records: int


class AdminAuditLogResponse(BaseModel):
    id: str
    org_id: str | None = None
    actor_user_id: str | None = None
    actor_email: str
    actor_name: str
    action: str
    category: str
    severity: str
    resource_type: str | None = None
    resource_id: str | None = None
    target_user_id: str | None = None
    target_user_email: str | None = None
    summary: str
    metadata: dict | None = None
    http_method: str | None = None
    http_path: str | None = None
    http_status: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime

    model_config = {"populate_by_name": True}


class AdminAuditLogListResponse(BaseModel):
    items: list[AdminAuditLogResponse]
    total: int
    page: int
    limit: int


class AdminAuditCategoryStat(BaseModel):
    category: str
    count: int


class AdminAuditActionStat(BaseModel):
    action: str
    count: int


class AdminAuditStatsResponse(BaseModel):
    total: int
    last_24h: int
    last_7d: int
    critical: int
    by_category: list[AdminAuditCategoryStat]
    top_actions: list[AdminAuditActionStat]


class AdminUserSummaryResponse(BaseModel):
    user_id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    joined_at: datetime
    chat_count: int
    turn_count: int
    has_brain: bool
    brain_lesson_count: int
    last_active_at: datetime | None = None


class AdminUserBrainSnapshot(BaseModel):
    summary: str = ""
    thinking_style: str = ""
    likes: list[str] = []
    dislikes: list[str] = []
    memories: list[dict] = []
    lesson_count: int = 0
    updated_at: datetime | None = None


class AdminUserDetailResponse(BaseModel):
    user_id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    joined_at: datetime
    created_at: datetime
    chat_count: int
    turn_count: int
    lesson_count: int
    brain: AdminUserBrainSnapshot


class AdminChatSummaryResponse(BaseModel):
    id: str
    title: str
    project_id: str | None = None
    created_by: str | None = None
    creator_name: str | None = None
    creator_email: str | None = None
    turn_count: int = 0
    created_at: datetime
    updated_at: datetime


class AdminChatDetailResponse(BaseModel):
    id: str
    title: str
    project_id: str | None = None
    created_by: str
    creator_name: str
    creator_email: str
    created_at: datetime
    updated_at: datetime
    turns: list[dict]


class AdminBrainSummaryResponse(BaseModel):
    user_id: str
    user_name: str
    email: str
    summary: str
    thinking_style: str
    likes: list[str] = []
    dislikes: list[str] = []
    memories_count: int
    lesson_count: int
    updated_at: datetime | None = None


class AdminBrainDetailResponse(BaseModel):
    user_id: str
    user_name: str
    email: str
    summary: str
    thinking_style: str
    likes: list[str] = []
    dislikes: list[str] = []
    memories: list[dict] = []
    lesson_count: int
    updated_at: datetime | None = None


class AdminLessonSummaryResponse(BaseModel):
    id: str
    title: str
    summary: str
    user_id: str
    user_name: str
    status: str
    chat_id: str
    turn_id: str
    created_at: datetime


class AdminProjectSummaryResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    chat_count: int
    created_at: datetime


class PricingCatalogItem(BaseModel):
    model_id: str
    openrouter_slug: str
    input_per_1k: float
    output_per_1k: float
    source: str


class PricingCatalogResponse(BaseModel):
    updated_at: datetime | None = None
    models: list[PricingCatalogItem]


# --- Transcriptions ---


class TranscriptionResponse(BaseModel):
    text: str
    language: str | None = None
    language_probability: float | None = None
    duration_seconds: float | None = None
    processing_seconds: float


# --- Share ---


class ShareLinkResponse(BaseModel):
    token: str
    url: str
    expires_at: datetime | None = None


class SharedChatResponse(BaseModel):
    title: str
    shared_by: str
    model_set_name: str
    turns: list[TurnResponse]


# --- Generic ---


class MessageResponse(BaseModel):
    message: str


# --- Brain ---


class BrainMemoryResponse(BaseModel):
    id: str
    source: str
    source_id: str | None = None
    title: str
    insight: str
    likes: list[str] = []
    dislikes: list[str] = []
    created_at: str | None = None


class BrainKnowledgeItemResponse(BaseModel):
    id: str
    source_type: str
    source_id: str
    title: str
    content: str
    project_id: str | None = None
    created_at: datetime | None = None


class BrainResponse(BaseModel):
    user_name: str
    summary: str
    thinking_style: str
    likes: list[str] = []
    dislikes: list[str] = []
    memories: list[BrainMemoryResponse] = []
    knowledge_items: list[BrainKnowledgeItemResponse] = []
    lesson_count: int = 0
    knowledge_count: int = 0
    updated_at: datetime | None = None


# --- Content labels & saved documents ---


class ContentLabelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class ContentLabelCreateRequest(BaseModel):
    name: str


class ContentLabelUpdateRequest(BaseModel):
    name: str


class SavedDocumentLabelBrief(BaseModel):
    id: str
    name: str


class SavedDocumentResponse(BaseModel):
    id: str
    name: str
    chat_id: str | None = None
    turn_id: str | None = None
    project_id: str | None = None
    chat_title: str = ""
    project_name: str | None = None
    labels: list[SavedDocumentLabelBrief] = []
    snapshot_json: dict = {}
    created_at: datetime
    updated_at: datetime


class SavedDocumentCreateRequest(BaseModel):
    turn_id: str
    name: str | None = None
    label_ids: list[str] = []
    label_names: list[str] = []


class SavedDocumentUpdateRequest(BaseModel):
    name: str | None = None
    label_ids: list[str] | None = None


class SavedDocumentSuggestRequest(BaseModel):
    turn_id: str


class SavedDocumentSuggestResponse(BaseModel):
    name: str
    label_suggestions: list[str] = []
