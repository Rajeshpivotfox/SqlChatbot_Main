from fastapi import APIRouter, Depends, HTTPException
from app.models.requests import QueryRequest
from app.models.responses import QueryResponse, ErrorResponse
from app.dependencies import get_orchestrator
from app.services.orchestrator import QueryOrchestrator
from app.exceptions import (
    SQLValidationError, QueryExecutionError, QueryTimeoutError, TableNotFoundError
)
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post(
    "/query",
    response_model=QueryResponse,
    responses={
        400: {"model": ErrorResponse},
        408: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def execute_query(
    request: QueryRequest,
    orchestrator: QueryOrchestrator = Depends(get_orchestrator),
):
    """Convert a natural language question to SQL and return results."""
    try:
        return await orchestrator.process_question(
            question=request.question,
            page=request.page,
            page_size=request.page_size,
            include_commentary=request.include_commentary,
        )
    except SQLValidationError as e:
        raise HTTPException(status_code=400, detail={
            "error_code": "INVALID_SQL",
            "message": str(e),
        })
    except TableNotFoundError as e:
        raise HTTPException(status_code=422, detail={
            "error_code": "TABLE_NOT_FOUND",
            "message": str(e),
        })
    except QueryTimeoutError as e:
        raise HTTPException(status_code=408, detail={
            "error_code": "QUERY_TIMEOUT",
            "message": str(e),
        })
    except QueryExecutionError as e:
        raise HTTPException(status_code=500, detail={
            "error_code": "EXECUTION_ERROR",
            "message": str(e),
        })
    except Exception as e:
        logger.exception("unhandled_error", error=str(e))
        raise HTTPException(status_code=500, detail={
            "error_code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred. Please try again.",
        })
