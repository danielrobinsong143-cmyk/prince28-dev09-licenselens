"""The worker process:  uv run arq licenselens.workers.main.WorkerSettings

It runs the same code as the API, with the same settings and the same database. The only
difference is where its work comes from: Redis instead of HTTP.
"""

import asyncio
import logging

from arq.worker import Worker

from licenselens.config import Settings, get_settings
from licenselens.db.ingest_jobs import IngestRepository
from licenselens.db.session import make_engine, make_sessionmaker
from licenselens.ingest.fetch import make_client
from licenselens.llm.doc_meta import DocMeta
from licenselens.llm.gateway import create_gateway
from licenselens.llm.prompts import latest, read_prompt
from licenselens.llm.recorder import LLMCallRecorder
from licenselens.logging_setup import setup_logging
from licenselens.workers.ingest import Pace, ingest_document
from licenselens.workers.queue import Queue, redis_settings

logger = logging.getLogger("licenselens.worker")


def _has_provider(gateway, settings: Settings) -> bool:
    if settings.groq_api_key or settings.gemini_api_key:
        return True
    primary = getattr(gateway, "primary", None)
    if primary is not None:
        api_key = getattr(primary, "api_key", None)
        if isinstance(api_key, str):
            return bool(api_key.strip())
        return True
    return False


async def startup(ctx: dict) -> None:
    settings: Settings = ctx["settings"]
    if not ctx.get("embedded"):
        setup_logging(settings.log_level)
        logging.getLogger("arq").handlers.clear()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    engine = make_engine(settings)
    ctx["engine"], ctx["sessionmaker"] = engine, make_sessionmaker(engine)
    ctx["recorder"] = LLMCallRecorder(
        ctx["sessionmaker"],
        settings,
    )
    ctx["gateway"] = create_gateway(settings, timeout=12.0, max_retries=0)
    ctx["http"] = make_client(2)
    ctx["pace"] = Pace(settings.ingest_rpm)
    ctx["extract"] = None
    if _has_provider(ctx["gateway"], settings):
        try:
            version = latest(settings.prompts_dir, "extract")
        except FileNotFoundError:
            logger.warning("no prompts/extract_v*.md: documents are parsed, not extracted")
        else:
            prompt = read_prompt(settings.prompts_dir, version)
            ctx["extract"] = {
                "schema": DocMeta,
                "prompt": prompt,
                "version": version,
                "chars": 6000,
            }
    # Redis may have forgotten jobs (a restart without persistence). Postgres has not.
    async with ctx["sessionmaker"]() as session:
        repo = IngestRepository(session)
        failed_ids, _ = await repo.recover_crashed_items(
            settings.ingest_max_tries,
            redis=ctx.get("redis"),
        )
        if failed_ids:
            logger.warning(
                "recovered %d crashed item(s) reaching max tries: %s marked failed",
                len(failed_ids),
                failed_ids,
            )
        lost = await Queue(ctx["redis"]).put(await repo.open_item_ids())
    logger.info("worker ready, %s unfinished item(s) put back in the queue", lost)


async def shutdown(ctx: dict) -> None:
    await ctx["http"].aclose()
    await ctx["engine"].dispose()


def worker_options(settings: Settings, *, embedded: bool = False) -> dict:
    return {
        "functions": [ingest_document],
        "on_startup": startup,
        "on_shutdown": shutdown,
        "redis_settings": redis_settings(settings.redis_url),
        "ctx": {"settings": settings, "embedded": embedded},
        "max_jobs": settings.ingest_concurrency,
        "job_timeout": settings.ingest_job_timeout,
        "max_tries": settings.ingest_max_tries,
        "keep_result": 0,
        "poll_delay": 1.0,
    }


async def run_embedded(settings: Settings) -> None:
    """A worker inside the API process, for hosts that offer no free worker service."""
    worker = Worker(**worker_options(settings, embedded=True), handle_signals=False)
    try:
        await worker.async_run()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("the embedded worker stopped: is REDIS_URL right?")
    finally:
        await worker.close()


# What the `arq` command reads. Evaluated dynamically on access so importing this
# module does not eagerly require environment variables or a local .env file.
def __getattr__(name: str):
    if name == "WorkerSettings":
        return worker_options(get_settings())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals().keys(), "WorkerSettings"])
