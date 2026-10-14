"""Application factory; run with ``uvicorn cache_service.main:create_app --factory``."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cache_service.api import router, validation_error_handler
from cache_service.caching import CachingTransformer
from cache_service.config import Settings, get_settings
from cache_service.repositories import PayloadRepository, TransformationRepository
from cache_service.service import PayloadService
from cache_service.transformer import SimulatedTransformer, Transformer


def create_app(
    settings: Settings | None = None, transformer: Transformer | None = None
) -> FastAPI:
    """Build the application.

    Args:
        settings: defaults to settings read from the environment.
        transformer: client of the transformer service; defaults to the
            simulated one. Tests pass a fake that counts calls.
    """
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        # One instance for the whole app: concurrent requests share its
        # in-flight transformer calls.
        caching_transformer = CachingTransformer(
            transformer or SimulatedTransformer(settings.transformer_latency),
            TransformationRepository(sessions),
            timeout=settings.transformer_timeout,
            max_concurrency=settings.transformer_max_concurrency,
        )
        app.state.sessions = sessions
        app.state.payload_service = PayloadService(
            caching_transformer, PayloadRepository(sessions)
        )
        try:
            yield
        finally:
            await caching_transformer.drain()
            await engine.dispose()

    app = FastAPI(
        title="Payload cache service",
        version="0.1.0",
        lifespan=lifespan,
        exception_handlers={RequestValidationError: validation_error_handler},
    )
    app.include_router(router)
    return app
