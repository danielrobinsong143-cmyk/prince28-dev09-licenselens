"""Unit tests for database session engine creation and connection retry logic."""

import socket
from unittest.mock import AsyncMock

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from licenselens.config import Settings
from licenselens.db.session import (
    connect_with_retry,
    extract_connect_args,
    make_engine,
    make_sessionmaker,
)


@pytest.fixture
def test_settings():
    db_url = (
        "postgresql://testuser:testpass"  # pragma: allowlist secret
        "@ep-test-123.ap-southeast-1.aws.neon.tech:5432/neondb?sslmode=require"
    )
    return Settings(
        app_env="ci",
        database_url=db_url,
        _env_file=None,
    )


async def test_dns_failure_followed_by_successful_connection():
    """Verify transient socket.gaierror is retried with backoff and succeeds on attempt 3."""
    attempts = 0
    slept: list[float] = []
    fake_conn = AsyncMock(spec=asyncpg.Connection)

    async def flaky_connect(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise socket.gaierror(11001, "getaddrinfo failed")
        return fake_conn

    async def fake_sleep(delay: float):
        slept.append(delay)

    connect_kwargs = {
        "host": "ep-test-123.ap-southeast-1.aws.neon.tech",
        "port": 5432,
        "user": "testuser",
        "password": "testpass",  # pragma: allowlist secret
        "database": "neondb",
        "ssl": "require",
    }

    result = await connect_with_retry(
        connect_kwargs,
        connect_fn=flaky_connect,
        sleep_fn=fake_sleep,
    )

    assert result is fake_conn
    assert attempts == 3
    assert slept == [0.2, 0.5]


async def test_persistent_dns_failure_after_all_retries():
    """Verify persistent socket.gaierror halts after exactly 3 total attempts and raises."""
    attempts = 0
    slept: list[float] = []

    async def always_failing_connect(**kwargs):
        nonlocal attempts
        attempts += 1
        raise socket.gaierror(11001, "getaddrinfo failed")

    async def fake_sleep(delay: float):
        slept.append(delay)

    connect_kwargs = {
        "host": "ep-test-123.ap-southeast-1.aws.neon.tech",
        "port": 5432,
        "user": "testuser",
        "password": "testpass",  # pragma: allowlist secret
        "database": "neondb",
    }

    with pytest.raises(socket.gaierror, match="getaddrinfo failed"):
        await connect_with_retry(
            connect_kwargs,
            connect_fn=always_failing_connect,
            sleep_fn=fake_sleep,
        )

    # Maximum 3 total attempts
    assert attempts == 3
    assert slept == [0.2, 0.5]


async def test_non_transient_authentication_failure_without_retry():
    """Verify non-transient authentication errors raise immediately on attempt 1 without retry."""
    attempts = 0
    slept: list[float] = []

    async def auth_failing_connect(**kwargs):
        nonlocal attempts
        attempts += 1
        raise asyncpg.InvalidPasswordError("password authentication failed for user 'testuser'")

    async def fake_sleep(delay: float):
        slept.append(delay)

    connect_kwargs = {
        "host": "ep-test-123.ap-southeast-1.aws.neon.tech",
        "port": 5432,
        "user": "testuser",
        "password": "wrongpassword",  # pragma: allowlist secret
        "database": "neondb",
    }

    with pytest.raises(asyncpg.InvalidPasswordError, match="password authentication failed"):
        await connect_with_retry(
            connect_kwargs,
            connect_fn=auth_failing_connect,
            sleep_fn=fake_sleep,
        )

    assert attempts == 1
    assert slept == []


def test_extract_connect_args_preserves_url_and_ssl_configuration(test_settings):
    """Verify all parameters including host, port, credentials, database, and SSL are preserved."""
    # Settings._asyncpg validator already ran to_asyncpg_url() on database_url
    assert "postgresql+asyncpg://" in test_settings.database_url
    assert "ssl=require" in test_settings.database_url

    kwargs = extract_connect_args(test_settings.database_url)

    assert kwargs["host"] == "ep-test-123.ap-southeast-1.aws.neon.tech"
    assert kwargs["port"] == 5432
    assert kwargs["user"] == "testuser"
    assert kwargs["password"] == "testpass"  # pragma: allowlist secret
    assert kwargs["database"] == "neondb"
    assert kwargs["ssl"] == "require"


def test_make_engine_preserves_pool_settings(test_settings):
    """Verify make_engine creates an AsyncEngine with configured pool settings."""
    engine = make_engine(test_settings)
    assert isinstance(engine, AsyncEngine)
    assert engine.pool.size() == test_settings.db_pool_size
    assert engine.pool._max_overflow == test_settings.db_max_overflow
    assert engine.pool._pre_ping is True
    assert engine.pool._recycle == 300

    sessionmaker = make_sessionmaker(engine)
    assert sessionmaker.kw.get("expire_on_commit") is False
