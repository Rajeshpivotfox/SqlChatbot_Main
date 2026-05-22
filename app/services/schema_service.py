# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA SERVICE — introspects SQL Server metadata and caches it.
#
# This service provides the database schema that gets embedded into the NL→SQL
# system prompt. Every column, data type, PK, and FK relationship is included
# so Claude can write correct JOINs and WHERE clauses.
#
# TOKEN EFFICIENCY:
#   - Schema is cached for 24 hours (schema_cache_ttl_seconds=86400).
#     Re-introspecting on every request would add ~200ms latency but doesn't
#     affect tokens — the schema data itself is what costs tokens.
#   - format_for_prompt() uses a compact one-line-per-table format:
#       [dbo].[tblName] (~1000 rows): Col1 (int*), Col2 (varchar -> dbo.Other.ID)
#     This is ~40-60% smaller than verbose multi-line formats.
#   - NLToSQLEngine._filter_relevant_tables() further prunes which tables are
#     included — this is where the real token savings happen.
#
# DOUBLE-CHECKED LOCKING: get_tables() checks the cache twice — once without
# the lock (fast path) and once inside the lock (prevents thundering herd when
# multiple requests arrive while the cache is expired).
# ──────────────────────────────────────────────────────────────────────────────

import asyncio
import time
import structlog
from app.infrastructure.database import DatabasePool
from app.models.responses import TableMetadata, ColumnMetadata

logger = structlog.get_logger(__name__)

# These queries run against sys.* views to discover tables, columns, PKs, and FKs
TABLES_QUERY = """
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    p.rows AS row_count
FROM sys.tables t
JOIN sys.schemas s ON t.schema_id = s.schema_id
JOIN sys.partitions p ON t.object_id = p.object_id AND p.index_id IN (0, 1)
WHERE s.name NOT IN ('sys')
ORDER BY s.name, t.name
"""

COLUMNS_QUERY = """
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    c.name AS column_name,
    ty.name AS data_type,
    c.is_nullable,
    c.max_length,
    CASE WHEN ic.object_id IS NOT NULL THEN 1 ELSE 0 END AS is_primary_key,
    CASE WHEN fkc.parent_object_id IS NOT NULL
         THEN OBJECT_SCHEMA_NAME(fkc.referenced_object_id) + '.' +
              OBJECT_NAME(fkc.referenced_object_id) + '.' +
              COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id)
         ELSE NULL
    END AS foreign_key_ref
FROM sys.columns c
JOIN sys.tables t ON c.object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
JOIN sys.types ty ON c.user_type_id = ty.user_type_id
LEFT JOIN (
    sys.index_columns ic
    JOIN sys.indexes i ON ic.object_id = i.object_id AND ic.index_id = i.index_id
                      AND i.is_primary_key = 1
) ON c.object_id = ic.object_id AND c.column_id = ic.column_id
LEFT JOIN sys.foreign_key_columns fkc
    ON c.object_id = fkc.parent_object_id AND c.column_id = fkc.parent_column_id
WHERE s.name NOT IN ('sys')
ORDER BY s.name, t.name, c.column_id
"""


class SchemaService:
    """Introspects SQL Server schema and caches the result (default 24h TTL)."""

    def __init__(self, db_pool: DatabasePool, cache_ttl: int = 86400):
        self._db_pool = db_pool
        self._cache_ttl = cache_ttl
        self._cached_tables: list[TableMetadata] | None = None
        self._cached_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_tables(self, force_refresh: bool = False) -> list[TableMetadata]:
        """Return cached schema, refreshing if stale or forced.
        Uses double-checked locking to prevent thundering herd."""
        # Fast path: cache is warm (no lock needed)
        if not force_refresh and self._cached_tables and \
           (time.time() - self._cached_at) < self._cache_ttl:
            return self._cached_tables

        # Slow path: acquire lock, check again, then introspect
        async with self._lock:
            if not force_refresh and self._cached_tables and \
               (time.time() - self._cached_at) < self._cache_ttl:
                return self._cached_tables

            tables = await self._introspect()
            self._cached_tables = tables
            self._cached_at = time.time()
            logger.info("schema_cache_refreshed", table_count=len(tables))
            return tables

    async def _introspect(self) -> list[TableMetadata]:
        """Query sys.* views to build a full schema model (tables + columns + FKs)."""
        loop = asyncio.get_event_loop()
        async with self._db_pool.acquire() as conn:
            table_rows = await loop.run_in_executor(
                None, lambda: conn.cursor().execute(TABLES_QUERY).fetchall()
            )
            col_rows = await loop.run_in_executor(
                None, lambda: conn.cursor().execute(COLUMNS_QUERY).fetchall()
            )

        # Group columns by (schema, table) for efficient lookup
        columns_by_table: dict[tuple[str, str], list[ColumnMetadata]] = {}
        for row in col_rows:
            key = (row.schema_name, row.table_name)
            col = ColumnMetadata(
                name=row.column_name,
                data_type=row.data_type,
                is_nullable=bool(row.is_nullable),
                is_primary_key=bool(row.is_primary_key),
                foreign_key_ref=row.foreign_key_ref,
            )
            columns_by_table.setdefault(key, []).append(col)

        tables = []
        for row in table_rows:
            key = (row.schema_name, row.table_name)
            tables.append(TableMetadata(
                schema_name=row.schema_name,
                table_name=row.table_name,
                columns=columns_by_table.get(key, []),
                row_count=row.row_count,
            ))
        return tables

    def format_for_prompt(self, tables: list[TableMetadata]) -> str:
        """Format schema as a compact one-line-per-table string for the LLM prompt.
        Format: [schema].[table] (~N rows): col1 (type*), col2 (type -> FK)
        The * marks primary keys; -> marks foreign key references.
        This compact format uses ~40-60% fewer tokens than verbose alternatives."""
        lines = []
        for t in tables:
            cols = ", ".join(
                f"{c.name} ({c.data_type}{'*' if c.is_primary_key else ''}"
                f"{' -> ' + c.foreign_key_ref if c.foreign_key_ref else ''})"
                for c in t.columns
            )
            lines.append(f"[{t.schema_name}].[{t.table_name}] "
                         f"(~{t.row_count} rows): {cols}")
        return "\n".join(lines)
