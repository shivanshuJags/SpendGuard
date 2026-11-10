"""Composition root: the one place where every component is built and wired.

Routes never construct their own dependencies. Tests call create_app() with a
fake gateway and an in-memory store, and exercise the very same routes.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import anthropic
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.catalog import Catalog
from app.adapters.paypal.client import PayPalClient
from app.adapters.store import Store
from app.agent.runner import AgentRunner
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
    agent: AgentRunner | None  # None when no Anthropic key is configured


def create_app(
    settings: Settings | None = None,
    gateway: PaymentGateway | None = None,
    store: Store | None = None,
    claude: object | None = None,  # tests pass a scripted fake
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
            owned_claude = None
            if claude is None and settings.anthropic_api_key:
                owned_claude = anthropic.AsyncAnthropic(
                    api_key=settings.anthropic_api_key.get_secret_value()
                )
            llm = claude or owned_claude
            runs = RunRegistry(db)
            app.state.container = Container(
                settings=settings,
                store=db,
                payments=PaymentService(db, paypal, settings.public_base_url),
                catalog=Catalog.load(settings.demo_merchant_email),
                runs=runs,
                agent=None
                if llm is None
                else AgentRunner(
                    llm,
                    model=settings.agent_model,
                    effort=settings.agent_effort,
                    max_steps=settings.agent_max_steps,
                ),
            )
            try:
                yield
            finally:
                await runs.shutdown()
                if owned_claude is not None:
                    await owned_claude.close()
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
