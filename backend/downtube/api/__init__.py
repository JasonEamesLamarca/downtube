"""REST API router registry. Each feature module exposes ``router``; register it here."""

from fastapi import APIRouter

from downtube.api import health

api_router = APIRouter()
api_router.include_router(health.router)
