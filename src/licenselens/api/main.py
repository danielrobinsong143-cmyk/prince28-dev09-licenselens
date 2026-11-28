"""App factory: build a fully wired FastAPI app from settings."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from licenselens.api.errors import register_error_handlers
from licenselens.api.middleware import request_context
from licenselens.api.routes import chat, documents, health, ingest
from licenselens.config import Settings, get_settings
from licenselens.db.session import make_engine, make_sessionmaker
from licenselens.llm.gateway import create_gateway
from licenselens.llm.recorder import LLMCallRecorder
from licenselens.logging_setup import setup_logging

logger = logging.getLogger("licenselens")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    engine = make_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("starting", extra={"status": settings.app_env})
        worker_task = None
        if settings.worker_in_api:
            from licenselens.workers.main import run_embedded

            worker_task = asyncio.create_task(run_embedded(settings))

        try:
            yield
        finally:
            if worker_task is not None:
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("embedded worker task failed during shutdown")

            queue = getattr(app.state, "queue", None)
            if queue is not None and hasattr(queue, "close"):
                res = queue.close()
                if asyncio.iscoroutine(res):
                    await res

            await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.llm_recorder = LLMCallRecorder(
        app.state.sessionmaker,
        settings,
    )
    app.state.llm_gateway = create_gateway(settings)

    app.middleware("http")(request_context)
    register_error_handlers(app)

    v1 = APIRouter(prefix="/v1")
    v1.include_router(documents.router)
    v1.include_router(chat.router)
    v1.include_router(ingest.router)

    app.include_router(health.router)
    app.include_router(v1)

    return app
