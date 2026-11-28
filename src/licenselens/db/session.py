"""One engine per app; one session per request."""

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import asyncpg
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from licenselens.config import Settings

logger = logging.getLogger(__name__)

MAX_CONNECT_ATTEMPTS = 3
INITIAL_RETRY_DELAY = 0.2
BACKOFF_FACTOR = 2.5

TRANSIENT_CONNECT_ERRORS = (
    socket.gaierror,
    TimeoutError,
    ConnectionResetError,
    ConnectionRefusedError,
    asyncpg.CannotConnectNowError,
    asyncpg.PostgresConnectionError,
)


def extract_connect_args(database_url: str) -> dict[str, Any]:
    """Extract connection parameters from database URL preserving SSL configuration."""
    url = make_url(database_url)
    connect_kwargs: dict[str, Any] = {
        "host": url.host,
        "port": url.port or 5432,
        "user": url.username,
        "password": url.password,
        "database": url.database,
    }
    query = dict(url.query)
    if "ssl" in query:
        connect_kwargs["ssl"] = query["ssl"]
    if "timeout" in query:
        with suppress(ValueError):
            connect_kwargs["timeout"] = float(query["timeout"])
    return connect_kwargs


async def connect_with_retry(
    connect_kwargs: dict[str, Any],
    *,
    max_attempts: int = MAX_CONNECT_ATTEMPTS,
    initial_delay: float = INITIAL_RETRY_DELAY,
    backoff_factor: float = BACKOFF_FACTOR,
    connect_fn: Callable[..., Awaitable[asyncpg.Connection]] = asyncpg.connect,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> asyncpg.Connection:
    """Connect to database with retries for transient DNS and network errors only."""
    delay = initial_delay
    target = f"{connect_kwargs.get('host')}:{connect_kwargs.get('port', 5432)}"

    for attempt in range(1, max_attempts + 1):
        try:
            return await connect_fn(**connect_kwargs)
        except TRANSIENT_CONNECT_ERRORS as exc:
            if attempt == max_attempts:
                logger.error(
                    "Database connection to %s failed permanently after %d attempts: %s",
                    target,
                    max_attempts,
                    exc,
                )
                raise
            logger.warning(
                "Transient database connect failure on %s (%s: %s). "
                "Retrying in %.2fs (attempt %d/%d)...",
                target,
                type(exc).__name__,
                exc,
                delay,
                attempt,
                max_attempts,
            )
            await sleep_fn(delay)
            delay *= backoff_factor

    raise RuntimeError("Unreachable: loop exited without return or exception")


def make_engine(settings: Settings) -> AsyncEngine:
    connect_kwargs = extract_connect_args(settings.database_url)

    async def async_creator() -> asyncpg.Connection:
        return await connect_with_retry(connect_kwargs)

    return create_async_engine(
        settings.database_url,
        async_creator=async_creator,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=300,
    )


def make_sessionmaker(
    engine: AsyncEngine,
) -> async_sessionmaker:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
