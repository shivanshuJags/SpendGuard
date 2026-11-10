"""Composition root: the one place where every component is built and wired.

Routes never construct their own dependencies. Tests call create_app() with a
fake gateway and an in-memory store, and exercise the very same routes.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.catalog import Catalog
from app.adapters.paypal.client import PayPalClient
from app.adapters.store import Store
from app.agent.base import AgentRunner
from app.agent.factory import agent_runner
from app.agent.runs import RunRegistry
from app.api.errors import install_error_handlers
from app.api.routes import router
from app.config import Settings, get_settings
from app.services.payments import PaymentService
from app.services.ports import PaymentGateway


@dataclass
class Container:
    settings: Settings
    store: Store
    payments: PaymentService
    catalog: Catalog
    runs: RunRegistry
    agent: AgentRunner | None  # None when the chosen provider has no API key


def create_app(
    settings: Settings | None = None,
    gateway: PaymentGateway | None = None,
    store: Store | None = None,
    llm: object | None = None,  # tests pass a scripted fake model client
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = store or Store(settings.database_path)
        async with httpx.AsyncClient(timeout=30) as http:
            paypal = gateway or PayPalClient(
                settings.paypal_client_id,
                settings.paypal_client_secret.get_secret_value(),
                settings.paypal_base_url,
                http,
            )
            runs = RunRegistry(db)
            async with agent_runner(settings, llm) as agent:
                app.state.container = Container(
                    settings=settings,
                    store=db,
                    payments=PaymentService(db, paypal, settings.public_base_url),
                    catalog=Catalog.load(settings.demo_merchant_email),
                    runs=runs,
                    agent=agent,
                )
                try:
                    yield
                finally:
                    await runs.shutdown()
        if store is None:  # close only what we opened
            db.close()

    app = FastAPI(title="SpendGuard", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(router)
    return app


def app() -> FastAPI:
    """Factory for `uvicorn app.main:app --factory`."""
    return create_app()
