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
    """Startup and shutdown lifecycle."""
    settings = get_settings()
    configure_logging(settings.log_level)
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
    app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

    return app


app = create_app()
