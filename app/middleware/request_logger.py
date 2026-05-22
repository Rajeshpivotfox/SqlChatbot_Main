# ──────────────────────────────────────────────────────────────────────────────
# REQUEST LOGGER — structured log for every HTTP request.
#
# Logs: method, path, status, duration_ms, client IP.
# Combined with the orchestrator's timing_breakdown, this gives full
# observability into where time is spent (network vs API vs DB).
# ──────────────────────────────────────────────────────────────────────────────

import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import structlog

logger = structlog.get_logger(__name__)


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """Logs every HTTP request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000

        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(elapsed, 2),
            client=request.client.host if request.client else "unknown",
        )
        return response
