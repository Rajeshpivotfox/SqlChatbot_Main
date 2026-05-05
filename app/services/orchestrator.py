import uuid
import time
import structlog
from app.services.nl_to_sql import NLToSQLEngine
from app.services.sql_validator import SQLValidator
from app.services.query_executor import QueryExecutor, QueryResult
from app.services.result_formatter import ResultFormatter
from app.services.commentary import CommentaryGenerator
from app.services.cache_service import CacheService
from app.models.responses import QueryResponse, ColumnInfo
from app.exceptions import OutOfScopeError

logger = structlog.get_logger(__name__)

OUT_OF_SCOPE_REPLY = (
    "I'm a database assistant for the RFDemoTest2 database and can only "
    "answer questions about your data.\n\n"
    "Try asking things like:\n"
    "• How many transactions are in the database?\n"
    "• What are the top 10 accounts by total value?\n"
    "• Show total value per legal entity\n"
    "• What exchange rates do we have?\n"
    "• Show transactions for period Jan2022"
)


def _ms(start: float) -> float:
    """Return elapsed milliseconds since start, rounded to 1 decimal."""
    return round((time.perf_counter() - start) * 1000, 1)


class QueryOrchestrator:
    """Coordinates the full NL-to-SQL-to-insight pipeline."""

    def __init__(
        self,
        nl_engine: NLToSQLEngine,
        validator: SQLValidator,
        executor: QueryExecutor,
        formatter: ResultFormatter,
        commentary_gen: CommentaryGenerator,
        cache: CacheService,
    ):
        self._nl_engine = nl_engine
        self._validator = validator
        self._executor = executor
        self._formatter = formatter
        self._commentary = commentary_gen
        self._cache = cache

    async def process_question(
        self,
        question: str,
        page: int = 1,
        page_size: int = 100,
        include_commentary: bool = True,
    ) -> QueryResponse:
        """Full pipeline: question -> SQL -> validate -> execute -> format -> comment."""
        query_id = str(uuid.uuid4())
        pipeline_start = time.perf_counter()
        timing: dict[str, float] = {}

        logger.info("pipeline_started", query_id=query_id, question=question)

        # ── Step 1: Cache check ───────────────────────────────────────────────
        t = time.perf_counter()
        cache_key = CacheService.make_key(question, page, page_size)
        cached = self._cache.get(cache_key)
        timing["cache_check_ms"] = _ms(t)

        if cached is not None:
            logger.info("pipeline_cache_hit", query_id=query_id)
            cached.query_id = query_id
            cached.timing_breakdown = {"cache_hit_ms": _ms(pipeline_start)}
            return cached

        # ── Step 2: NL → SQL (Claude API) ────────────────────────────────────
        t = time.perf_counter()
        try:
            sql = await self._nl_engine.generate_sql(question)
        except OutOfScopeError as e:
            timing["nl_to_sql_ms"] = _ms(t)
            timing["total_ms"] = _ms(pipeline_start)
            logger.info("pipeline_out_of_scope", query_id=query_id,
                        question=question, has_answer=bool(e.answer), **timing)
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

        # ── Step 3: SQL Validation ────────────────────────────────────────────
        t = time.perf_counter()
        validated_sql = self._validator.validate(sql)
        timing["validation_ms"] = _ms(t)

        # ── Step 4: Query Execution ───────────────────────────────────────────
        t = time.perf_counter()
        result: QueryResult = await self._executor.execute(
            validated_sql, page=page, page_size=page_size
        )
        timing["sql_execution_ms"] = _ms(t)

        # ── Step 5: Result Formatting ─────────────────────────────────────────
        t = time.perf_counter()
        formatted = self._formatter.format_for_response(result)
        timing["formatting_ms"] = _ms(t)

        # ── Step 6: Commentary (Claude API) ───────────────────────────────────
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

        # ── Step 8: Cache ─────────────────────────────────────────────────────
        self._cache.set(cache_key, response)
        return response
