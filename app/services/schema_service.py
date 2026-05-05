import asyncio
import time
import structlog
from app.infrastructure.database import DatabasePool
from app.models.responses import TableMetadata, ColumnMetadata

logger = structlog.get_logger(__name__)

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
    """Introspects SQL Server schema and caches the result."""

    def __init__(self, db_pool: DatabasePool, cache_ttl: int = 86400):
        self._db_pool = db_pool
        self._cache_ttl = cache_ttl
        self._cached_tables: list[TableMetadata] | None = None
        self._cached_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_tables(self, force_refresh: bool = False) -> list[TableMetadata]:
        """Return cached schema, refreshing if stale or forced."""
        if not force_refresh and self._cached_tables and \
           (time.time() - self._cached_at) < self._cache_ttl:
            return self._cached_tables

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
        loop = asyncio.get_event_loop()
        async with self._db_pool.acquire() as conn:
            table_rows = await loop.run_in_executor(
                None, lambda: conn.cursor().execute(TABLES_QUERY).fetchall()
            )
            col_rows = await loop.run_in_executor(
                None, lambda: conn.cursor().execute(COLUMNS_QUERY).fetchall()
            )

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
        """Format schema metadata as a compact string for LLM context."""
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
