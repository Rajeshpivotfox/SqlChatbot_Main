# ──────────────────────────────────────────────────────────────────────────────
# APPLICATION ENTRY POINT
#
# Architecture overview (request flow):
#   Browser → FastAPI (this file) → /api/v1/query endpoint → QueryOrchestrator
#     → NLToSQLEngine (Claude API call #1: NL→SQL)
#     → SQLValidator (regex-based safety check, no API call)
#     → QueryExecutor (runs SQL against SQL Server)
#     → TemplateCommentary (deterministic, 0 tokens) OR CommentaryGenerator (Claude API call #2)
#     → Response with data + insight
#
# Token budget: each question costs 1-2 Claude API calls.
#   Call #1 (NL→SQL): system prompt with DB schema + few-shot examples + history
#   Call #2 (Commentary): only if TemplateCommentary can't handle the result shape
#   The CacheService skips BOTH calls on repeated questions.
# ──────────────────────────────────────────────────────────────────────────────

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import get_settings
from app.api.router import api_router
from app.dependencies import init_services, shutdown_services
from app.middleware.rate_limiter import RateLimiterMiddleware
from app.middleware.request_logger import RequestLoggerMiddleware
from app.logging_config import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: DB pool + schema cache warm-up. Shutdown: close connections."""
    settings = get_settings()
    configure_logging(settings.log_level)
    # init_services() wires all singletons and pre-caches the DB schema
    await init_services(settings)
    yield
    await shutdown_services()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        lifespan=lifespan,
    )

    # Middleware order matters: outermost runs first
    # RequestLogger → RateLimiter → CORS → route handler
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimiterMiddleware,
                       max_requests=settings.rate_limit_requests)
    app.add_middleware(RequestLoggerMiddleware)

    app.include_router(api_router, prefix=settings.api_prefix)
    # Static files serve the chatbot UI (HTML/JS/CSS)
    app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

    return app


app = create_app()
