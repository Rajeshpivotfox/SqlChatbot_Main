# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — all settings loaded from .env via pydantic-settings.
# Fields use alias= so they map to ENV_VAR_NAMES while keeping snake_case in code.
#
# TOKEN-EFFICIENCY KNOBS (tune these to control Claude API costs):
#   claude_max_tokens  — max output tokens per call. SQL output is ~100-300 tokens;
#                        4096 is overkill for NL→SQL. The ClaudeClient overrides this
#                        per-call (512 for SQL, 1024 for commentary).
#   claude_temperature — 0.0 for deterministic SQL; commentary uses 0.3 override.
#   cache_ttl_seconds  — how long to cache full responses (skips both API calls).
#   schema_cache_ttl   — how long to cache DB schema (avoids re-introspecting).
# ──────────────────────────────────────────────────────────────────────────────

from pydantic_settings import BaseSettings
from pydantic import Field, SecretStr
from functools import lru_cache


class Settings(BaseSettings):
    # Application
    app_name: str = "SQLChatbot"
    debug: bool = False
    api_prefix: str = "/api/v1"
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    # Database (SQL Server via pyodbc)
    db_server: str = Field(..., alias="DB_SERVER")
    db_name: str = Field(..., alias="DB_NAME")
    db_user: str = Field(..., alias="DB_USER")
    db_password: SecretStr = Field(..., alias="DB_PASSWORD")
    db_driver: str = Field(default="ODBC Driver 17 for SQL Server", alias="DB_DRIVER")
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_query_timeout: int = Field(default=30, alias="DB_QUERY_TIMEOUT_SECONDS")

    # Claude API — controls the Anthropic SDK calls
    anthropic_api_key: SecretStr = Field(..., alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-sonnet-4-20250514", alias="CLAUDE_MODEL")
    # This is the global default; per-call overrides in ClaudeClient.complete() are lower
    claude_max_tokens: int = Field(default=4096, alias="CLAUDE_MAX_TOKENS")
    claude_temperature: float = Field(default=0.0, alias="CLAUDE_TEMPERATURE")
    claude_max_retries: int = Field(default=3, alias="CLAUDE_MAX_RETRIES")

    # Cache — prevents repeated Claude API calls for identical questions
    cache_ttl_seconds: int = Field(default=3600, alias="CACHE_TTL_SECONDS")
    schema_cache_ttl_seconds: int = Field(default=86400, alias="SCHEMA_CACHE_TTL_SECONDS")

    # Rate Limiting — per-IP sliding window
    rate_limit_requests: int = Field(default=30, alias="RATE_LIMIT_REQUESTS_PER_MINUTE")

    # Pagination — controls how many DB rows are returned per page
    default_page_size: int = Field(default=100, alias="DEFAULT_PAGE_SIZE")
    max_page_size: int = Field(default=5000, alias="MAX_PAGE_SIZE")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "populate_by_name": True}


@lru_cache
def get_settings() -> Settings:
    """Singleton — cached after first call, never re-reads .env."""
    return Settings()
