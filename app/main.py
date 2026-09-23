"""
PineLab Backend — FastAPI application entrypoint.

This is the foundation stage (Part 4): the app wires up CORS, the
standard error envelope, the /api/v1 mount point, and a basic health
check. No business routers are registered yet — see app/api/v1/router.py.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.error_handlers import register_error_handlers

settings = get_settings()

app = FastAPI(
    title="PineLab Backend",
    description="Backend API for the PineLab helpdesk-automation console.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["health"])
def health_check() -> dict:
    """Basic liveness check. Deliberately does not touch the database."""
    return {"status": "ok"}
