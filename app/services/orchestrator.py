# ──────────────────────────────────────────────────────────────────────────────
# QUERY ORCHESTRATOR — the central pipeline that wires everything together.
#
# Pipeline steps (in order):
#   1. Cache check       — if hit, skip ALL remaining steps (0 API calls, 0 DB calls)
#   2. NL → SQL          — Claude API call #1 (most expensive step, ~500-2000 input tokens)
#   3. SQL validation     — regex-only, no API call, blocks writes/injection
#   4. Query execution    — runs validated SELECT against SQL Server
#   5. Result formatting  — converts pyodbc rows to JSON-serializable dicts
#   6. Commentary         — TemplateCommentary (0 tokens) or Claude API call #2
#   7. Cache store        — saves response for future identical questions
#   8. Memory store       — appends turn to conversation history (for follow-ups)
#
# TOKEN COST PER QUESTION:
#   Cache hit:    0 tokens
#   Simple query: ~1500 input + ~200 output (template commentary, 1 API call)
#   Complex query: ~2500 input + ~500 output (LLM commentary, 2 API calls)
# ──────────────────────────────────────────────────────────────────────────────

import uuid
import time
import structlog
from app.services.nl_to_sql import NLToSQLEngine
from app.services.sql_validator import SQLValidator
from app.services.query_executor import QueryExecutor, QueryResult
from app.services.result_formatter import ResultFormatter
from app.services.commentary import CommentaryGenerator
from app.services.cache_service import CacheService
from app.services.conversation_memory import ConversationMemory
from app.models.responses import QueryResponse, ColumnInfo
from app.exceptions import OutOfScopeError

logger = structlog.get_logger(__name__)

OUT_OF_SCOPE_REPLY = (
    "I'm a database assistant for the zdb_employee database and can only "
    "answer questions about your transactional data.\n\n"
    "Try asking things like:\n"
    "• How many transactions are in the database?\n"
    "• What are the top 10 accounts by total value?\n"
    "• Show total value per legal entity\n"
    "• What is the total liability?\n"
    "• Show transactions for year 2022\n"
    "• Show monthly totals for January"
)


def _ms(start: float) -> float:
    """Return elapsed milliseconds since start, rounded to 1 decimal."""
    return round((time.perf_counter() - start) * 1000, 1)


class QueryOrchestrator:
    """Coordinates the full NL → SQL → execute → insight pipeline."""

    def __init__(
        self,
        nl_engine: NLToSQLEngine,
        validator: SQLValidator,
        executor: QueryExecutor,
        formatter: ResultFormatter,
        commentary_gen: CommentaryGenerator,
        cache: CacheService,
        memory: ConversationMemory,
    ):
        self._nl_engine = nl_engine
        self._validator = validator
        self._executor = executor
        self._formatter = formatter
        self._commentary = commentary_gen
        self._cache = cache
        self._memory = memory

    async def process_question(
        self,
        question: str,
        page: int = 1,
        page_size: int = 100,
        include_commentary: bool = True,
        session_id: str | None = None,
    ) -> QueryResponse:
        """Full pipeline: question → SQL → validate → execute → format → comment.
        Each step is timed; the timing_breakdown dict is returned to the client."""
        query_id = str(uuid.uuid4())
        pipeline_start = time.perf_counter()
        timing: dict[str, float] = {}

        logger.info("pipeline_started", query_id=query_id, question=question)

        # ── Step 1: Cache check (0 tokens, 0 DB calls) ───────────────────────
        # Cache key = SHA-256(question + page + page_size). Identical questions
        # skip the entire pipeline — this is the #1 token saver for repeat queries.
        t = time.perf_counter()
        cache_key = CacheService.make_key(question, page, page_size)
        cached = self._cache.get(cache_key)
        timing["cache_check_ms"] = _ms(t)

        if cached is not None:
            logger.info("pipeline_cache_hit", query_id=query_id)
            cached.query_id = query_id
            cached.timing_breakdown = {"cache_hit_ms": _ms(pipeline_start)}
            return cached

        # ── Step 2: NL → SQL (Claude API call #1 — most expensive) ───────────
        # Sends: system prompt (schema + rules + examples + history) + question
        # Returns: raw SQL string or "OUT_OF_SCOPE" for non-DB questions
        t = time.perf_counter()
        history = self._memory.get_history(session_id) if session_id else []
        try:
            sql = await self._nl_engine.generate_sql(question, history=history)
        except OutOfScopeError as e:
            timing["nl_to_sql_ms"] = _ms(t)
            timing["total_ms"] = _ms(pipeline_start)
            logger.info("pipeline_out_of_scope", query_id=query_id,
                        question=question, has_answer=bool(e.answer), **timing)
            # OUT_OF_SCOPE:<answer> means Claude answered a general knowledge Q
            if e.answer:
                commentary = (
                    f"{e.answer}\n\n"
                    "---\n"
                    "*For database queries, try asking about your transactions, "
                    "accounts, or financial data.*"
                )
            else:
                commentary = OUT_OF_SCOPE_REPLY
            return QueryResponse(
                query_id=query_id,
                question=question,
                generated_sql="",
                columns=[],
                rows=[],
                total_rows=0,
                page=page,
                page_size=page_size,
                has_more=False,
                out_of_scope=True,
                commentary=commentary,
                execution_time_ms=timing["total_ms"],
                timing_breakdown=timing,
            )
        timing["nl_to_sql_ms"] = _ms(t)

        # ── Step 3: SQL Validation (regex-only, 0 tokens) ────────────────────
        # Blocks non-SELECT statements, SQL injection patterns, oversized queries
        t = time.perf_counter()
        validated_sql = self._validator.validate(sql)
        timing["validation_ms"] = _ms(t)

        # ── Step 4: Query Execution (DB call, no tokens) ─────────────────────
        # Runs the validated SQL against SQL Server with pagination + timeout
        t = time.perf_counter()
        result: QueryResult = await self._executor.execute(
            validated_sql, page=page, page_size=page_size
        )
        timing["sql_execution_ms"] = _ms(t)

        # ── Step 5: Result Formatting (CPU-only, 0 tokens) ──────────────────
        # Converts pyodbc rows → JSON-serializable dicts, handles Decimal/datetime
        t = time.perf_counter()
        formatted = self._formatter.format_for_response(result)
        timing["formatting_ms"] = _ms(t)

        # ── Step 6: Commentary (0 tokens if template handles it, else API call #2)
        # TemplateCommentary handles ~60-70% of queries (single values, GROUP BY,
        # TOP N) without any API call. Only complex/unrecognised shapes hit Claude.
        commentary = None
        if include_commentary:
            t = time.perf_counter()
            commentary = await self._commentary.generate(
                question, validated_sql, result
            )
            timing["commentary_ms"] = _ms(t)

        # ── Step 7: Totals ────────────────────────────────────────────────────
        timing["total_ms"] = _ms(pipeline_start)

        logger.info(
            "pipeline_completed",
            query_id=query_id,
            **timing,
        )

        response = QueryResponse(
            query_id=query_id,
            question=question,
            generated_sql=validated_sql,
            columns=[ColumnInfo(**c) for c in formatted["columns"]],
            rows=formatted["rows"],
            total_rows=formatted["total_rows"],
            page=page,
            page_size=page_size,
            has_more=(page * page_size) < formatted["total_rows"],
            commentary=commentary,
            execution_time_ms=round(timing["total_ms"], 2),
            timing_breakdown=timing,
        )

        # ── Step 8: Cache response (saves tokens on repeat questions) ─────────
        self._cache.set(cache_key, response)

        # ── Step 9: Store turn in conversation memory (for follow-ups) ────────
        # Only store on page 1 — pagination requests for the same question
        # shouldn't create duplicate history entries
        if session_id and page == 1:
            self._memory.add_turn(
                session_id=session_id,
                question=question,
                sql=validated_sql,
                row_count=formatted["total_rows"],
            )

        return response
