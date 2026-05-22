# ──────────────────────────────────────────────────────────────────────────────
# RESPONSE MODELS — shared Pydantic models for API responses and internal use.
#
# QueryResponse: returned to the frontend on every /query call.
#   - timing_breakdown shows per-step durations (useful for identifying
#     whether the bottleneck is Claude API, DB, or formatting)
#   - out_of_scope=true means the question wasn't about the database
#
# TableMetadata / ColumnMetadata: used internally by SchemaService and also
#   exposed via the /schema endpoint. These represent the DB schema that
#   gets embedded into the NL→SQL prompt.
# ──────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel, Field
from datetime import datetime


class ColumnInfo(BaseModel):
    """Column metadata in query results (name + Python type name)."""
    name: str
    type: str


class QueryResponse(BaseModel):
    """Full pipeline response: SQL + data rows + optional commentary + timing."""
    query_id: str
    question: str
    generated_sql: str
    columns: list[ColumnInfo]
    rows: list[dict]
    total_rows: int
    page: int
    page_size: int
    has_more: bool
    out_of_scope: bool = False
    commentary: str | None = None
    execution_time_ms: float
    timing_breakdown: dict[str, float] = Field(
        default_factory=dict,
        description="Per-step durations in milliseconds"
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    detail: str | None = None


class TableMetadata(BaseModel):
    """One table's metadata from SQL Server sys.* views."""
    schema_name: str
    table_name: str
    columns: list["ColumnMetadata"]
    row_count: int | None = None
    description: str | None = None


class ColumnMetadata(BaseModel):
    """One column's metadata. FK ref format: schema.table.column."""
    name: str
    data_type: str
    is_nullable: bool
    is_primary_key: bool = False
    foreign_key_ref: str | None = None
    description: str | None = None


class SchemaResponse(BaseModel):
    """Response for the /schema endpoint."""
    tables: list[TableMetadata]
    last_refreshed: datetime
