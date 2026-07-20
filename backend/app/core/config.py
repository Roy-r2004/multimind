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

    # Redis / job queue
    redis_url: str = Field(default="redis://localhost:6379/0")
    scraping_mock_step_delay_ms: int = 600
    scraping_worker_concurrency: int = 4
    scraping_execution_stale_seconds: int = 120
    scraping_worker_job_timeout_seconds: int = 1800

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
    serper_search_results_per_query: int = 5
    serper_search_max_queries_per_discovery: int = 2

    # Secure source retrieval
    source_retrieval_user_agent: str = "MultiMindSourceRetrieval/1.0 (+https://multimind.local/source-retrieval)"
    source_retrieval_timeout_seconds: float = 15.0
    source_retrieval_connect_timeout_seconds: float = 5.0
    source_retrieval_max_redirects: int = 5
    source_retrieval_max_bytes: int = 2_097_152
    source_retrieval_allowed_ports: Annotated[list[int], NoDecode] = Field(default=[80, 443])
    source_retrieval_robots_policy: Literal["respect"] = "respect"

    # Optional source discovery provider — Brave Search
    brave_search_api_key: str | None = None
    brave_search_base_url: str = "https://api.search.brave.com/res/v1/web/search"
    brave_search_timeout_seconds: float = 20.0
    brave_search_results_per_query: int = 10
    brave_search_max_queries_per_discovery: int = 6

    # Public URL for share links
    public_app_url: str = Field(default="http://localhost:5173")

    # Prompts
    prompts_dir: str = "app/prompts"

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

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
