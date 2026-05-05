from pydantic_settings import BaseSettings
from pydantic import Field, SecretStr
from functools import lru_cache


class Settings(BaseSettings):
    # Application
    app_name: str = "SQLChatbot"
    debug: bool = False
    api_prefix: str = "/api/v1"
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    # Database
    db_server: str = Field(..., alias="DB_SERVER")
    db_name: str = Field(..., alias="DB_NAME")
    db_user: str = Field(..., alias="DB_USER")
    db_password: SecretStr = Field(..., alias="DB_PASSWORD")
    db_driver: str = Field(default="ODBC Driver 17 for SQL Server", alias="DB_DRIVER")
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_query_timeout: int = Field(default=30, alias="DB_QUERY_TIMEOUT_SECONDS")

    # Claude API
    anthropic_api_key: SecretStr = Field(..., alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-sonnet-4-20250514", alias="CLAUDE_MODEL")
    claude_max_tokens: int = Field(default=4096, alias="CLAUDE_MAX_TOKENS")
    claude_temperature: float = Field(default=0.0, alias="CLAUDE_TEMPERATURE")
    claude_max_retries: int = Field(default=3, alias="CLAUDE_MAX_RETRIES")

    # Cache
    cache_ttl_seconds: int = Field(default=3600, alias="CACHE_TTL_SECONDS")
    schema_cache_ttl_seconds: int = Field(default=86400, alias="SCHEMA_CACHE_TTL_SECONDS")

    # Rate Limiting
    rate_limit_requests: int = Field(default=30, alias="RATE_LIMIT_REQUESTS_PER_MINUTE")

    # Pagination
    default_page_size: int = Field(default=100, alias="DEFAULT_PAGE_SIZE")
    max_page_size: int = Field(default=5000, alias="MAX_PAGE_SIZE")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "populate_by_name": True}


@lru_cache
def get_settings() -> Settings:
    return Settings()
