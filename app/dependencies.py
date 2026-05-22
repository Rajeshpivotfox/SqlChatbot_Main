# ──────────────────────────────────────────────────────────────────────────────
# DEPENDENCY INJECTION — wires all singletons at startup.
#
# Called from main.py lifespan(). Creates the full service graph:
#
#   Settings (.env)
#     ├── DatabasePool (pyodbc connection pool)
#     │     ├── SchemaService (introspects DB schema, caches 24h)
#     │     └── QueryExecutor (runs validated SQL with timeout)
#     ├── ClaudeClient (Anthropic SDK wrapper)
#     │     ├── NLToSQLEngine (question → SQL, API call #1)
#     │     └── CommentaryGenerator (results → insight, API call #2)
#     └── QueryOrchestrator (wires the full pipeline)
#           ├── CacheService (in-memory, skips both API calls on repeat questions)
#           ├── ConversationMemory (per-session history for follow-ups)
#           ├── SQLValidator (regex safety gate, 0 tokens)
#           └── ResultFormatter (JSON serialization, 0 tokens)
#
# The schema cache is warmed at startup (get_tables()) so the first user
# request doesn't pay the introspection cost.
# ──────────────────────────────────────────────────────────────────────────────

from app.config import Settings
from app.infrastructure.database import DatabasePool
from app.infrastructure.claude_client import ClaudeClient
from app.services.schema_service import SchemaService
from app.services.nl_to_sql import NLToSQLEngine
from app.services.sql_validator import SQLValidator
from app.services.query_executor import QueryExecutor
from app.services.result_formatter import ResultFormatter
from app.services.commentary import CommentaryGenerator
from app.services.cache_service import CacheService
from app.services.conversation_memory import ConversationMemory
from app.services.orchestrator import QueryOrchestrator

# Module-level singletons (initialized in lifespan, accessed via getters below)
_db_pool: DatabasePool | None = None
_claude_client: ClaudeClient | None = None
_orchestrator: QueryOrchestrator | None = None
_schema_service: SchemaService | None = None


async def init_services(settings: Settings) -> None:
    """Initialize all singletons at startup. Called once from lifespan()."""
    global _db_pool, _claude_client, _orchestrator, _schema_service

    # 1. Database connection pool
    conn_str = DatabasePool.build_connection_string(
        server=settings.db_server,
        database=settings.db_name,
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
        driver=settings.db_driver,
    )
    _db_pool = DatabasePool(conn_str, pool_size=settings.db_pool_size,
                            query_timeout=settings.db_query_timeout)
    await _db_pool.initialize()

    # 2. Claude API client (shared by NLToSQLEngine and CommentaryGenerator)
    _claude_client = ClaudeClient(
        api_key=settings.anthropic_api_key.get_secret_value(),
        model=settings.claude_model,
        max_tokens=settings.claude_max_tokens,
        temperature=settings.claude_temperature,
        max_retries=settings.claude_max_retries,
    )

    # 3. Wire the service graph
    _schema_service = SchemaService(_db_pool, settings.schema_cache_ttl_seconds)
    nl_engine = NLToSQLEngine(_claude_client, _schema_service)
    validator = SQLValidator()
    executor = QueryExecutor(_db_pool, settings.db_query_timeout)
    formatter = ResultFormatter()
    commentary_gen = CommentaryGenerator(_claude_client, formatter)
    cache = CacheService(ttl_seconds=settings.cache_ttl_seconds)
    memory = ConversationMemory()

    _orchestrator = QueryOrchestrator(
        nl_engine=nl_engine,
        validator=validator,
        executor=executor,
        formatter=formatter,
        commentary_gen=commentary_gen,
        cache=cache,
        memory=memory,
    )

    # 4. Warm the schema cache so the first request is fast
    await _schema_service.get_tables()


async def shutdown_services() -> None:
    """Gracefully close DB pool and Claude client."""
    global _db_pool, _claude_client
    if _db_pool:
        await _db_pool.close()
    if _claude_client:
        await _claude_client.close()


def get_orchestrator() -> QueryOrchestrator:
    """FastAPI dependency — returns the initialized orchestrator singleton."""
    assert _orchestrator is not None, "Services not initialized"
    return _orchestrator


def get_schema_service() -> SchemaService:
    """FastAPI dependency — returns the initialized schema service singleton."""
    assert _schema_service is not None, "Services not initialized"
    return _schema_service
