import os

from fastapi import APIRouter

from . import agent, bots, commerce, fulfillment, messages, optimization, providers, stores, users, webhooks

api_router = APIRouter()

api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(stores.router, prefix="/stores", tags=["stores"])
api_router.include_router(providers.router, prefix="/providers", tags=["providers"])
api_router.include_router(bots.router, prefix="/bots", tags=["bots"])
api_router.include_router(messages.router, prefix="/messages", tags=["messages"])
api_router.include_router(commerce.router, prefix="/commerce", tags=["commerce"])
api_router.include_router(optimization.router, prefix="/optimize", tags=["optimization"])
api_router.include_router(fulfillment.router, prefix="/fulfillment", tags=["fulfillment"])
api_router.include_router(agent.router, prefix="/agent", tags=["agent"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
