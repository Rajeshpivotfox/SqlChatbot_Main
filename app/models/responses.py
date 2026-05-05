from pydantic import BaseModel, Field
from datetime import datetime


class ColumnInfo(BaseModel):
    name: str
    type: str


class QueryResponse(BaseModel):
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
    schema_name: str
    table_name: str
    columns: list["ColumnMetadata"]
    row_count: int | None = None
    description: str | None = None


class ColumnMetadata(BaseModel):
    name: str
    data_type: str
    is_nullable: bool
    is_primary_key: bool = False
    foreign_key_ref: str | None = None
    description: str | None = None


class SchemaResponse(BaseModel):
    tables: list[TableMetadata]
    last_refreshed: datetime
