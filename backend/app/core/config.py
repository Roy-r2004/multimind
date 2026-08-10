"""Application configuration — environment-driven, validated via Pydantic Settings."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "MultiAI API"
    app_version: str = "1.0.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:5173", "http://localhost:3000"]
    )

    # Security
    secret_key: str = Field(default="change-me-in-production-use-openssl-rand-hex-32")
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    algorithm: str = "HS256"

    # Database — SQLite for local dev, PostgreSQL for production
    database_url: str = Field(default="sqlite+aiosqlite:///./multiai.db")
    # SQLAlchemy's 5+10 default is too small: a single execution page fires 9 parallel
    # requests, so a few concurrent viewers would queue past pool_timeout and 500.
    database_pool_size: int = 20
    database_max_overflow: int = 20
    database_pool_timeout_seconds: int = 15
    # Recycle below typical proxy/Postgres idle cutoffs so pooled sockets never go stale.
    database_pool_recycle_seconds: int = 1800

    # Redis / job queue
    redis_url: str = Field(default="redis://localhost:6379/0")
    scraping_mock_step_delay_ms: int = 600
    scraping_worker_concurrency: int = 4
    # Long LLM phases (extract/cleanup) can exceed 2 minutes without task heartbeats.
    scraping_execution_stale_seconds: int = 900
    # Full-census discovery alone can exceed 30m; keep ARQ job alive for multi-hour runs.
    scraping_worker_job_timeout_seconds: int = 21600
    # When true (default in development), run scrapes inside the API process if Redis/worker is down
    scraping_inline_execution: bool | None = None

    # LLM — OpenRouter (single key for all models)
    openrouter_api_key: str | None = None
    openrouter_site_url: str | None = None
    openrouter_app_name: str = "MultiAI"
    openrouter_pricing_cache_ttl_seconds: int = 3600
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2

    # Real source discovery
    source_discovery_provider: str = "serper"
    serper_api_key: str | None = None
    serper_search_base_url: str = "https://google.serper.dev/search"
    serper_search_timeout_seconds: float = 10.0
    serper_search_results_per_query: int = 10
    serper_search_max_queries_per_discovery: int = 4

    # Secure source retrieval
    source_retrieval_user_agent: str = "MultiMindSourceRetrieval/1.0 (+https://multimind.local/source-retrieval)"
    source_retrieval_timeout_seconds: float = 15.0
    source_retrieval_connect_timeout_seconds: float = 5.0
    source_retrieval_max_redirects: int = 5
    source_retrieval_max_bytes: int = 2_097_152
    source_retrieval_allowed_ports: Annotated[list[int], NoDecode] = Field(default=[80, 443])
    source_retrieval_robots_policy: Literal["respect"] = "respect"
    source_retrieval_max_candidates_per_coverage_cell: int = 10
    source_retrieval_max_candidates_per_execution: int = 150
    # Phase B1: same-domain contact/location page crawl budgets
    contact_crawl_max_pages_real: int = 3
    contact_crawl_max_pages_full_census: int = 8
    contact_crawl_max_depth: int = 1
    contact_crawl_max_bytes_per_page: int = 1_048_576

    # Real facility extraction (worker-connected)
    facility_extraction_enabled: bool = True
    facility_extraction_model: str = "gpt-4.1"
    facility_extraction_max_document_characters: int = 200_000
    facility_extraction_chunk_characters: int = 12_000
    facility_extraction_chunk_overlap_characters: int = 500
    facility_extraction_max_chunks_per_document: int = 20
    facility_extraction_max_candidates_per_chunk: int = 25
    facility_extraction_max_candidates_per_document: int = 100
    # Country demos need dozens of pages; 3 was a smoke-test cap that starved results.
    facility_extraction_max_documents_per_execution: int = 50
    facility_extraction_max_chunks_per_execution: int = 120
    facility_extraction_timeout_seconds: float = 60.0
    facility_extraction_max_attempts: int = 2
    facility_extraction_max_evidence_quote_characters: int = 1000

    # Auto-publish verified staging candidates into final rehab tables
    facility_publication_enabled: bool = True
    facility_publication_min_confidence: float = 0.55
    facility_publication_max_candidates_per_execution: int = 300
    facility_publication_duplicate_match_threshold: float = 0.82

    # Post-publication official website lookup
    facility_website_enrichment_enabled: bool = True
    facility_website_enrichment_max_facilities_per_execution: int = 300
    facility_website_enrichment_results_per_facility: int = 5
    facility_website_enrichment_timeout_seconds: float = 20.0

    # Final AI roster cleanup (non-rehabs, bad links, duplicates, website fixes)
    facility_ai_cleanup_enabled: bool = True
    facility_ai_cleanup_model: str = "gpt-4.1"
    facility_ai_cleanup_batch_size: int = 12

    # Optional source discovery provider — Brave Search
    brave_search_api_key: str | None = None
    brave_search_base_url: str = "https://api.search.brave.com/res/v1/web/search"
    brave_search_timeout_seconds: float = 20.0
    brave_search_results_per_query: int = 10
    brave_search_max_queries_per_discovery: int = 6

    # Standalone Google Places Maps census (separate from the scraping pipeline)
    google_places_enabled: bool = True
    google_places_api_key: str | None = None
    google_places_base_url: str = "https://places.googleapis.com/v1"
    google_places_timeout_seconds: float = 20.0
    maps_census_model: str = "gpt-4.1"
    # Strict keep/drop gate: one low-cost call per facility, Sonar fallback only
    # when evidence is insufficient or confidence is below the threshold.
    maps_keep_drop_model: str = "gpt-5-nano"
    maps_keep_drop_confidence_threshold: float = 0.85
    maps_keep_drop_sonar_fallback_enabled: bool = True
    maps_keep_drop_concurrency: int = 5
    maps_keep_drop_batch_size: int = 25
    maps_keep_drop_max_crawl_excerpt_chars: int = 4000
    maps_keep_drop_place_timeout_seconds: float = 60.0
    maps_census_max_cells_per_run: int = 1500
    maps_census_max_cells_per_campaign: int = 1500
    maps_census_saturation_window: int = 10
    maps_census_min_new_unique_for_expansion: int = 3
    maps_census_min_new_plausible_for_expansion: int = 1
    maps_census_max_places_per_cell: int = 20
    maps_census_classification_batch_size: int = 15
    # Google Places (New) pagination — each cell can page beyond the first 20
    # results up to a bounded number of pages before triggering subdivision.
    maps_census_max_pages_per_cell: int = 3
    maps_census_places_page_size: int = 20
    # Independent per-page retry budget (backoff+jitter), separate from the
    # single-page tenacity retry used by the legacy search_text() path.
    maps_census_places_page_max_attempts: int = 3
    # Resumable cell claiming (SELECT ... SKIP LOCKED on Postgres; guarded
    # atomic UPDATE elsewhere) so multiple workers never double-process a cell.
    maps_census_cell_stale_seconds: int = 300
    maps_census_cell_max_attempts: int = 3
    # Cap on how deep capped-cell subdivision can recurse before giving up on
    # a single geography instead of subdividing forever.
    maps_census_max_subdivision_depth: int = 2
    # Fallback website discovery for relevant places Google Places returned with no
    # (or an untrustworthy) website. "llm" asks Claude directly; "serper" uses Google
    # search candidates plus optional Claude selection (legacy).
    maps_census_website_search_enabled: bool = True
    maps_census_website_search_mode: str = "llm"
    # Legacy single-pass cap retained for backward compatibility; the resumable
    # loop below now processes every pending place in batches instead of
    # silently truncating at this number.
    maps_census_website_search_max_places_per_run: int = 250
    # Resumable website-search batch processing (Phase 2 gap #4): pulls pending
    # places in fixed-size batches until none remain or the call budget is hit.
    maps_census_website_search_batch_size: int = 25
    maps_census_website_search_max_calls_per_run: int = 5000
    maps_census_website_llm_enabled: bool = True

    # === PHASE 0: SAFETY GUARDS (Production Stability) ===
    # Circuit breaker: prevent cascading failures from external APIs
    maps_circuit_breaker_enabled: bool = True
    maps_circuit_breaker_failure_threshold: int = 5
    maps_circuit_breaker_recovery_timeout_seconds: int = 60

    # LLM call budget per cell (prevent cost spiral)
    maps_cell_llm_budget_max_calls: int = 10
    maps_cell_llm_budget_classification_cost: int = 1
    maps_cell_llm_budget_enrichment_cost: int = 2

    # LLM call budget per run (hard ceiling)
    maps_run_llm_budget_max_calls: int = 5000
    maps_run_llm_budget_check_enabled: bool = True

    # Global campaign timeout (prevent runaway jobs)
    maps_census_campaign_timeout_seconds: int = 28800  # 8 hours
    maps_census_timeout_check_interval_cells: int = 10  # Check every N cells

    # Observability: structured logging
    maps_observability_enabled: bool = True
    maps_observability_log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    maps_census_website_llm_model: str = "sonar-pro"
    maps_census_website_llm_batch_size: int = 3
    maps_census_website_llm_min_confidence: float = 0.75
    maps_census_website_llm_verify_reachable: bool = True
    maps_census_website_llm_timeout_seconds: float = 120.0
    # Background cron: automatically retry the website search for completed runs that
    # still have gaps, so nobody has to click a "find missing websites" button.
    maps_census_auto_website_refresh_enabled: bool = True
    maps_census_auto_website_refresh_cooldown_hours: int = 6
    maps_census_auto_website_refresh_max_attempts: int = 3

    # Pexels — country hero photography for the Maps Census UI (never sent to the browser)
    pexels_api_key: str | None = None
    pexels_search_timeout_seconds: float = 6.0
    # Local disk cache for Google Places facility photos (fetched once, reused after)
    maps_census_photo_cache_dir: str = "data/maps_photos"

    # Phase 2: live-web country discovery profile, built once per run before grid planning
    maps_census_country_profile_model: str = "sonar-pro"
    maps_census_country_profile_timeout_seconds: float = 90.0

    # Phase 2: AI web-search enrichment of addictions/languages (no crawling)
    maps_census_enrichment_enabled: bool = True
    # Legacy single-pass cap retained for backward compatibility; the resumable
    # loop below now processes every pending place in batches instead of
    # silently truncating at this number.
    maps_census_enrichment_max_places_per_run: int = 250
    # LLM request grouping — how many facilities go into one Sonar prompt.
    # Delivery requirement: one facility per Sonar call, so a single bad
    # response/parse failure never drags down siblings in the same call.
    maps_census_enrichment_batch_size: int = 1
    # Resumable enrichment batch processing (Phase 2 gap #4): how many pending
    # places the outer loop pulls per iteration before checking call budget.
    maps_census_enrichment_processing_batch_size: int = 25
    maps_cascade_place_timeout_seconds: float = 300.0
    # Phase 1 classification is metadata-first, so a single slow domain must not
    # consume a large place budget and stall its whole batch.
    maps_classification_place_timeout_seconds: float = 90.0
    maps_classification_crawl_timeout_seconds: float = 45.0
    maps_detail_place_timeout_seconds: float = 120.0
    maps_place_max_attempts: int = 2
    maps_census_enrichment_max_calls_per_run: int = 5000
    maps_census_enrichment_model: str = "sonar-pro"
    # Combined budget across ALL crawled pages for one place (see
    # combined_excerpt in maps_website_crawl_service.py, which appends pages
    # in order and truncates once this is exceeded). 1200 was too small for
    # multi-page crawls — a facility's contact page alone (multiple
    # locations, phone/email/hours/form) easily exceeds it, and whatever
    # appears later in the combined text (often the phone number) silently
    # gets truncated away before the LLM ever sees it, even though earlier
    # content (e.g. an email link) survives. Raised to align with the other
    # crawl-excerpt budgets in this file (keep-drop uses 4000, structured
    # classification uses 20000) — 1200 was a clear outlier.
    maps_census_enrichment_max_crawl_excerpt_chars: int = 4000
    # Multi-location brand disaggregation: during detail enrichment, check
    # whether a kept facility's own website reveals other in-country physical
    # locations of the same organization not yet discovered, and add them as
    # new candidates for the strict keep/drop gate. Gated on a cheap keyword
    # pre-filter (see contains_multi_location_signal), so this only costs an
    # extra call when the crawled text actually hints at multiple locations.
    maps_sibling_location_extraction_enabled: bool = True
    maps_census_auto_enrichment_enabled: bool = True
    maps_census_cascade_enrichment_enabled: bool = True
    maps_census_two_phase_pipeline_enabled: bool = True
    # Delivery reliability: run classification/detail enrichment as many short-lived
    # ARQ jobs (one small batch each, self-enqueuing) instead of one long-lived job
    # that holds hundreds of places and loses everything on a worker crash.
    maps_census_small_batch_enrichment_enabled: bool = True
    maps_classification_batch_size: int = 20
    maps_detail_enrichment_batch_size: int = 10
    # Watchdog: how stale the enrichment heartbeat must be before auto-requeuing.
    maps_enrichment_watchdog_stale_minutes: int = 5
    maps_enrichment_batch_lock_ttl_seconds: int = 600

    # Cascaded enrichment: bounded crawl context for primary extraction
    maps_crawl_max_relevant_pages: int = 5
    maps_crawl_max_chars_per_page: int = 6000
    maps_crawl_max_total_context_chars: int = 20000

    # Primary structured extraction (cheaper than Sonar)
    maps_primary_extraction_provider: str = "openrouter"
    maps_primary_extraction_model: str = "gpt-4.1-mini"
    maps_primary_extraction_concurrency: int = 5
    maps_primary_extraction_max_retries: int = 2
    maps_primary_extraction_max_calls_per_run: int = 5000
    maps_primary_extraction_confidence_threshold: float = 0.55

    # Sonar classification budget (Phase 1 Layer 4) — separate from detail enrichment
    maps_sonar_fallback_enabled: bool = True
    maps_sonar_fallback_max_percent: float = 20.0
    maps_sonar_fallback_max_per_campaign: int = 200
    maps_sonar_fallback_concurrency: int = 5
    # Detail enrichment (Phase 2) uses maps_census_enrichment_* call budget independently.
    # Classification Sonar budget exhaustion must not abort Phase 2.

    # Phase 3: limited official-site crawl before structured enrichment
    maps_census_website_crawl_enabled: bool = True
    maps_census_website_crawl_max_pages_per_domain: int = 5
    maps_census_website_crawl_max_bytes_per_page: int = 524_288
    maps_census_website_crawl_timeout_seconds: float = 15.0
    maps_census_website_crawl_connect_timeout_seconds: float = 5.0
    maps_census_website_crawl_cache_ttl_hours: int = 168
    maps_census_website_crawl_max_excerpt_chars: int = 4000
    maps_census_website_crawl_user_agent: str = (
        "MultiMindMapsCrawl/1.0 (+https://multimind.local/maps-census-crawl)"
    )
    maps_census_website_crawl_max_redirects: int = 5

    # Phase 3: optional external directory discovery (stub sources only)
    maps_census_external_discovery_enabled: bool = False
    maps_census_external_discovery_max_sources: int = 5
    maps_census_external_discovery_max_candidates_per_source: int = 50

    # Phase 4: Maps census admin UI (dashboard, review actions, campaign controls)
    maps_census_admin_ui_enabled: bool = True

    # Public URL for share links
    public_app_url: str = Field(default="http://localhost:5173")

    # Prompts
    prompts_dir: str = "app/prompts"

    # Transcription — faster-whisper
    transcription_enabled: bool = True
    transcription_model: str = "large-v3-turbo"
    transcription_device: Literal["auto", "cpu", "cuda"] = "cpu"
    transcription_compute_type: str = "float16"
    transcription_cpu_model: str = "medium"
    transcription_cpu_compute_type: str = "int8"
    transcription_strict_device: bool = False
    transcription_max_duration_seconds: int = Field(default=1800, gt=0)
    transcription_max_upload_bytes: int = Field(default=78_643_200, gt=0)
    transcription_timeout_seconds: float = Field(default=900.0, gt=0)
    transcription_tmp_dir: str = "/tmp/multimind-transcriptions"
    transcription_concurrency: int = Field(default=1, ge=1)
    transcription_model_cache_dir: str = "/models/whisper"
    transcription_beam_size: int = Field(default=1, ge=1)
    transcription_vad_filter: bool = True
    transcription_initial_prompt: str = (
        "MultiMind, OpenRouter, Ollama, Claude, Gemini, GPT, RAG, orchestrator, "
        "scraper, blueprint, verdict, model set"
    )

    # Chat attachments
    chat_attachment_max_bytes: int = Field(default=10 * 1024 * 1024)  # 10 MB
    chat_attachment_dir: str = "data/chat_attachments"
    chat_attachment_context_max_chars: int = Field(default=50_000)
    # Catalog model id used for the single pre-council image analysis call.
    # Default: gemini (google/gemini-2.5-pro) — strong multimodal quality for
    # screenshots, UI, tables, charts, and photos at a reasonable OpenRouter cost.
    # Override with CHAT_IMAGE_ANALYSIS_MODEL (e.g. gpt-4.1, gpt-4.1-mini).
    chat_image_analysis_model: str = "gemini"
    library_file_dir: str = "data/library_files"
    library_file_max_bytes: int = Field(default=10 * 1024 * 1024)  # 10 MB

    # Rate limiting
    rate_limit_per_minute: int = 60

    # Observability
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("source_retrieval_allowed_ports", mode="before")
    @classmethod
    def parse_source_retrieval_allowed_ports(cls, v: str | list[int]) -> list[int]:
        if isinstance(v, str):
            return [int(port.strip()) for port in v.split(",") if port.strip()]
        return v

    @field_validator(
        "facility_extraction_max_document_characters",
        "facility_extraction_chunk_characters",
        "facility_extraction_max_chunks_per_document",
        "facility_extraction_max_documents_per_execution",
        "facility_extraction_max_chunks_per_execution",
        "facility_extraction_max_candidates_per_chunk",
        "facility_extraction_max_candidates_per_document",
        "facility_extraction_max_evidence_quote_characters",
    )
    @classmethod
    def validate_positive_bounded_extraction_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("facility extraction limits must be positive")
        if value > 1_000_000:
            raise ValueError("facility extraction limits must be bounded")
        return value

    @field_validator("facility_extraction_chunk_overlap_characters")
    @classmethod
    def validate_non_negative_overlap(cls, value: int) -> int:
        if value < 0:
            raise ValueError("facility extraction chunk overlap must be non-negative")
        return value

    @field_validator("facility_extraction_max_attempts")
    @classmethod
    def validate_bounded_extraction_attempts(cls, value: int) -> int:
        if value <= 0 or value > 5:
            raise ValueError("facility extraction attempts must be between 1 and 5")
        return value

    @field_validator("facility_extraction_timeout_seconds")
    @classmethod
    def validate_bounded_extraction_timeout(cls, value: float) -> float:
        if value <= 0 or value > 300:
            raise ValueError("facility extraction timeout must be between 0 and 300 seconds")
        return value

    @field_validator("facility_extraction_chunk_overlap_characters")
    @classmethod
    def validate_overlap_smaller_than_chunk(cls, value: int, info) -> int:
        chunk = info.data.get("facility_extraction_chunk_characters")
        if chunk is not None and value >= chunk:
            raise ValueError("facility extraction chunk overlap must be smaller than chunk size")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
