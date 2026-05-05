import asyncio
import re
import time
import structlog
from app.infrastructure.database import DatabasePool
from app.exceptions import QueryExecutionError, QueryTimeoutError, TableNotFoundError

logger = structlog.get_logger(__name__)


def _extract_invalid_table(error_msg: str) -> str | None:
    m = re.search(r"Invalid object name '([^']+)'", error_msg, re.IGNORECASE)
    return m.group(1) if m else None


class QueryResult:
    """Structured result of a SQL query execution."""

    def __init__(self, columns: list[dict], rows: list[dict],
                 total_rows: int, execution_time_ms: float):
        self.columns = columns
        self.rows = rows
        self.total_rows = total_rows
        self.execution_time_ms = execution_time_ms


class QueryExecutor:
    """Executes validated SQL against the database with timeout and pagination."""

    def __init__(self, db_pool: DatabasePool, query_timeout: int = 30):
        self._db_pool = db_pool
        self._query_timeout = query_timeout

    async def execute(self, sql: str, page: int = 1,
                      page_size: int = 100) -> QueryResult:
        """Execute a read-only SQL query with pagination."""
        start = time.perf_counter()
        loop = asyncio.get_event_loop()

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._execute_sync, sql, page, page_size
                ),
                timeout=self._query_timeout,
            )
            elapsed = (time.perf_counter() - start) * 1000
            result.execution_time_ms = elapsed
            logger.info("query_executed",
                        rows=len(result.rows),
                        total=result.total_rows,
                        time_ms=round(elapsed, 2))
            return result

        except asyncio.TimeoutError:
            logger.error("query_timeout", sql=sql[:200],
                         timeout=self._query_timeout)
            raise QueryTimeoutError(
                f"Query timed out after {self._query_timeout} seconds"
            )
        except (QueryTimeoutError, QueryExecutionError, TableNotFoundError):
            raise
        except Exception as e:
            table = _extract_invalid_table(str(e))
            if table:
                raise TableNotFoundError(table)
            logger.error("query_execution_error", error=str(e), sql=sql[:200])
            raise QueryExecutionError(f"Query execution failed: {str(e)}")

    def _execute_sync(self, sql: str, page: int, page_size: int) -> QueryResult:
        """Synchronous query execution (runs in thread pool)."""
        import pyodbc

        conn_string = self._db_pool._connection_string
        with pyodbc.connect(conn_string, timeout=self._query_timeout) as conn:
            conn.autocommit = True
            cursor = conn.cursor()

            # Get total count
            count_sql = f"SELECT COUNT(*) AS total FROM ({sql}) AS _count_subq"
            try:
                cursor.execute(count_sql)
                total_rows = cursor.fetchone()[0]
            except pyodbc.Error:
                total_rows = -1

            # Try paginated execution with OFFSET/FETCH
            paginated_sql = (
                f"{sql} "
                f"OFFSET {(page - 1) * page_size} ROWS "
                f"FETCH NEXT {page_size} ROWS ONLY"
            )

            try:
                cursor.execute(paginated_sql)
            except pyodbc.Error:
                # Fallback: execute original query, slice in Python
                cursor.execute(sql)
                all_rows = cursor.fetchall()
                total_rows = len(all_rows)
                columns = [
                    {"name": desc[0], "type": desc[1].__name__ if desc[1] else "str"}
                    for desc in cursor.description
                ]
                start_idx = (page - 1) * page_size
                sliced = all_rows[start_idx:start_idx + page_size]
                rows = [
                    dict(zip([c["name"] for c in columns], row))
                    for row in sliced
                ]
                return QueryResult(columns, rows, total_rows, 0.0)

            columns = [
                {"name": desc[0], "type": desc[1].__name__ if desc[1] else "str"}
                for desc in cursor.description
            ]
            rows = [
                dict(zip([c["name"] for c in columns], row))
                for row in cursor.fetchall()
            ]
            return QueryResult(columns, rows, total_rows, 0.0)
