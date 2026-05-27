# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA SERVICE — introspects SQL Server metadata and caches it.
#
# This service provides the database schema that gets embedded into the NL→SQL
# system prompt. Discovers BOTH tables AND views using sys.objects.
#
# DATABASE: zdb_employee contains only the [dbo].[transactionaldata] VIEW
# (no base tables). The old queries used sys.tables which only returns tables,
# so we now use sys.objects with type IN ('U', 'V') to find both.
#
# TOKEN EFFICIENCY:
#   - Schema is cached for 24 hours (schema_cache_ttl_seconds=86400).
#   - format_for_prompt() uses a compact one-line-per-object format.
#   - With a single view, the entire schema section is ~80 tokens.
#
# DOUBLE-CHECKED LOCKING: get_tables() checks the cache twice — once without
# the lock (fast path) and once inside the lock (prevents thundering herd).
# ──────────────────────────────────────────────────────────────────────────────

import asyncio
import time
import structlog
from app.infrastructure.database import DatabasePool
from app.models.responses import TableMetadata, ColumnMetadata

logger = structlog.get_logger(__name__)

# ── Object discovery query ───────────────────────────────────────────────────
# Uses sys.objects to find BOTH tables (type='U') AND views (type='V').
# Row count: tables have partition stats; views use sp_spaceused estimate or NULL.
# LEFT JOIN on sys.partitions handles views gracefully (they have no partitions).
OBJECTS_QUERY = """
SELECT
    s.name AS schema_name,
    o.name AS table_name,
    o.type AS object_type,
    p.rows AS row_count
FROM sys.objects o
JOIN sys.schemas s ON o.schema_id = s.schema_id
LEFT JOIN sys.partitions p ON o.object_id = p.object_id AND p.index_id IN (0, 1)
WHERE o.type IN ('U', 'V')
  AND s.name NOT IN ('sys')
ORDER BY s.name, o.name
"""

# ── Column discovery query ───────────────────────────────────────────────────
# Uses sys.objects instead of sys.tables so it finds columns for VIEWS too.
# PK and FK detection still works for tables; views will have is_primary_key=0
# and foreign_key_ref=NULL (views don't have their own constraints).
COLUMNS_QUERY = """
SELECT
    s.name AS schema_name,
    o.name AS table_name,
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
JOIN sys.objects o ON c.object_id = o.object_id AND o.type IN ('U', 'V')
JOIN sys.schemas s ON o.schema_id = s.schema_id
JOIN sys.types ty ON c.user_type_id = ty.user_type_id
LEFT JOIN (
    sys.index_columns ic
    JOIN sys.indexes i ON ic.object_id = i.object_id AND ic.index_id = i.index_id
                      AND i.is_primary_key = 1
) ON c.object_id = ic.object_id AND c.column_id = ic.column_id
LEFT JOIN sys.foreign_key_columns fkc
    ON c.object_id = fkc.parent_object_id AND c.column_id = fkc.parent_column_id
WHERE s.name NOT IN ('sys')
ORDER BY s.name, o.name, c.column_id
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
        """Query sys.objects to discover tables + views, then get their columns."""
        loop = asyncio.get_event_loop()
        async with self._db_pool.acquire() as conn:
            # Discover all tables and views
            table_rows = await loop.run_in_executor(
                None, lambda: conn.cursor().execute(OBJECTS_QUERY).fetchall()
            )
            # Get columns for all discovered objects
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

        # Build list; for views, row_count from partitions is NULL so we
        # fetch it with a COUNT(*) query during introspection (runs once per cache TTL)
        tables = []
        views_needing_count: list[tuple[int, str, str]] = []
        for row in table_rows:
            key = (row.schema_name, row.table_name)
            idx = len(tables)
            tables.append(TableMetadata(
                schema_name=row.schema_name,
                table_name=row.table_name,
                columns=columns_by_table.get(key, []),
                row_count=row.row_count,
            ))
            # Views have NULL row_count from sys.partitions
            if row.row_count is None and row.object_type.strip() == 'V':
                views_needing_count.append((idx, row.schema_name, row.table_name))

        # Get actual row counts for views (only runs at startup / cache refresh)
        if views_needing_count:
            async with self._db_pool.acquire() as conn:
                for idx, schema, name in views_needing_count:
                    try:
                        count_sql = f"SELECT COUNT(*) FROM [{schema}].[{name}]"
                        count_row = await loop.run_in_executor(
                            None, lambda s=count_sql: conn.cursor().execute(s).fetchone()
                        )
                        tables[idx].row_count = count_row[0] if count_row else 0
                    except Exception:
                        tables[idx].row_count = 0

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
