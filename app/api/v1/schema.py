from fastapi import APIRouter, Depends, Query
from app.models.responses import SchemaResponse
from app.dependencies import get_schema_service
from app.services.schema_service import SchemaService
from datetime import datetime

router = APIRouter()


@router.get("/schema", response_model=SchemaResponse)
async def get_schema(
    refresh: bool = Query(False, description="Force schema refresh"),
    schema_service: SchemaService = Depends(get_schema_service),
):
    """Return the database schema metadata."""
    tables = await schema_service.get_tables(force_refresh=refresh)
    return SchemaResponse(
        tables=tables,
        last_refreshed=datetime.utcnow(),
    )
