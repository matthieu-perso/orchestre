import os

from fastapi import APIRouter

from . import bots, messages, providers, users

api_router = APIRouter()

api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(providers.router, prefix="/providers", tags=["providers"])
api_router.include_router(bots.router, prefix="/bots", tags=["bots"])
api_router.include_router(messages.router, prefix="/messages", tags=["messages"])
