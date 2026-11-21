"""REST API router registry. Each feature module exposes ``router``; register it here."""

from fastapi import APIRouter

from downtube.api import auth, dashboard, downloads, health, library, likes, settings, subscriptions

api_router = APIRouter()
api_router.include_router(health.router)
for _module in (auth, dashboard, subscriptions, likes, downloads, library, settings):
    api_router.include_router(_module.router)
