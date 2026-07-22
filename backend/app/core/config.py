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

    # LLM — OpenRouter (single key for all models)
    openrouter_api_key: str | None = None
    openrouter_site_url: str | None = None
    openrouter_app_name: str = "MultiAI"
    openrouter_pricing_cache_ttl_seconds: int = 3600
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2

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
    transcription_max_duration_seconds: int = Field(default=600, gt=0)
    transcription_max_upload_bytes: int = Field(default=26_214_400, gt=0)
    transcription_timeout_seconds: float = Field(default=300.0, gt=0)
    transcription_tmp_dir: str = "/tmp/multimind-transcriptions"
    transcription_concurrency: int = Field(default=1, ge=1)
    transcription_model_cache_dir: str = "/models/whisper"
    transcription_beam_size: int = Field(default=1, ge=1)
    transcription_vad_filter: bool = True
    transcription_initial_prompt: str = (
        "MultiMind, OpenRouter, Ollama, Claude, Gemini, GPT, RAG, orchestrator, "
        "scraper, blueprint, verdict, model set"
    )

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

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
