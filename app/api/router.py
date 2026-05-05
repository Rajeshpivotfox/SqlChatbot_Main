from fastapi import APIRouter
from app.api.v1 import query, schema, health

api_router = APIRouter()
api_router.include_router(query.router, tags=["Query"])
api_router.include_router(schema.router, tags=["Schema"])
api_router.include_router(health.router, tags=["Health"])
