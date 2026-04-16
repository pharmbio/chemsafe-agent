"""Shared async PostgreSQL connection pool with resilient reconnects."""

from __future__ import annotations

import asyncio
from typing import Optional, Sequence, Tuple

from psycopg import InterfaceError, OperationalError, errors as pg_errors
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolClosed, PoolTimeout

from app.config import (
    DATABASE_URL,
    POSTGRES_POOL_MAX_SIZE,
    POSTGRES_POOL_MIN_SIZE,
    POSTGRES_POOL_TIMEOUT,
    logger,
)

DEFAULT_RETRY_ATTEMPTS = 5
DEFAULT_RETRY_INITIAL_DELAY = 0.5
DEFAULT_RETRY_MAX_DELAY = 5.0

DEFAULT_RETRYABLE_EXCEPTIONS: Tuple[type[BaseException], ...] = (
    OperationalError,
    InterfaceError,
    pg_errors.AdminShutdown,
    pg_errors.CannotConnectNow,
    pg_errors.ConnectionDoesNotExist,
    pg_errors.ConnectionException,
    pg_errors.ConnectionFailure,
    pg_errors.ConnectionTimeout,
    pg_errors.CrashShutdown,
    pg_errors.DiskFull,
    pg_errors.ReadOnlySqlTransaction,
    PoolClosed,
    PoolTimeout,
    ConnectionResetError,
    ConnectionAbortedError,
    TimeoutError,
    OSError,
)


class _ResilientConnectionContext:
    """Async context manager that retries acquiring a connection with backoff."""

    __slots__ = ("_acquire_cb", "_args", "_kwargs", "_pool", "_inner_cm")

    def __init__(self, acquire_cb, pool, args, kwargs):
        self._acquire_cb = acquire_cb
        self._args = args
        self._kwargs = kwargs
        self._pool: ResilientAsyncConnectionPool = pool
        self._inner_cm = None

    async def __aenter__(self):
        delay = self._pool._retry_initial_delay
        last_exc: Optional[BaseException] = None

        for attempt in range(1, self._pool._retry_attempts + 1):
            try:
                self._inner_cm = self._acquire_cb(*self._args, **self._kwargs)
                conn = await self._inner_cm.__aenter__()
                if attempt > 1:
                    logger.info("PostgreSQL connection re-established after %s attempts", attempt)
                return conn
            except asyncio.CancelledError:
                raise
            except self._pool._retryable_errors as exc:
                last_exc = exc
                if attempt == self._pool._retry_attempts:
                    break

                logger.warning(
                    "PostgreSQL connection attempt %s/%s failed: %s",
                    attempt,
                    self._pool._retry_attempts,
                    exc,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._pool._retry_max_delay)
            except Exception:
                raise

        if last_exc:
            logger.error("Unable to acquire PostgreSQL connection after %s attempts", self._pool._retry_attempts)
            raise last_exc
        raise RuntimeError("Failed to acquire PostgreSQL connection without explicit exception")

    async def __aexit__(self, exc_type, exc, tb):
        if self._inner_cm is None:
            return False
        return await self._inner_cm.__aexit__(exc_type, exc, tb)


class ResilientAsyncConnectionPool(AsyncConnectionPool):
    """AsyncConnectionPool that retries connection acquisition to survive DB restarts."""

    def __init__(
        self,
        *args,
        acquire_retries: int = DEFAULT_RETRY_ATTEMPTS,
        retry_initial_delay: float = DEFAULT_RETRY_INITIAL_DELAY,
        retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY,
        retryable_errors: Optional[Sequence[type[BaseException]]] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._retry_attempts = max(1, acquire_retries)
        self._retry_initial_delay = max(0.05, retry_initial_delay)
        self._retry_max_delay = max(self._retry_initial_delay, retry_max_delay)
        self._retryable_errors: Tuple[type[BaseException], ...] = tuple(
            retryable_errors or DEFAULT_RETRYABLE_EXCEPTIONS
        )

    def connection(self, *args, **kwargs):
        acquire_cb = super().connection
        return _ResilientConnectionContext(acquire_cb, self, args, kwargs)


_pool: Optional[ResilientAsyncConnectionPool] = None
_pool_lock: Optional[asyncio.Lock] = None


async def get_async_pool() -> ResilientAsyncConnectionPool:
    """Return a singleton async connection pool."""

    global _pool
    if _pool and not _pool.closed:
        return _pool

    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()

    async with _pool_lock:
        if _pool and not _pool.closed:
            return _pool

        if not DATABASE_URL:
            raise ValueError("DATABASE_URL environment variable is required for PostgreSQL persistence")

        conninfo = DATABASE_URL
        if "application_name" not in conninfo:
            separator = "&" if "?" in conninfo else "?"
            conninfo = f"{conninfo}{separator}application_name=chemsafe_agent"

        kwargs = {
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        }

        pool = ResilientAsyncConnectionPool(
            conninfo=conninfo,
            kwargs=kwargs,
            min_size=POSTGRES_POOL_MIN_SIZE,
            max_size=POSTGRES_POOL_MAX_SIZE,
            max_idle=300.0,
            max_lifetime=3600.0,
            timeout=float(POSTGRES_POOL_TIMEOUT),
            reconnect_timeout=300.0,
            open=False,
            acquire_retries=7,
            retry_initial_delay=0.5,
            retry_max_delay=8.0,
        )

        await pool.open()
        _pool = pool
        logger.info("PostgreSQL pool initialized")
        return _pool


async def close_async_pool() -> None:
    """Close the shared pool when the app shuts down."""

    global _pool
    if _pool and not _pool.closed:
        await _pool.close()
        logger.info("PostgreSQL pool closed")
    _pool = None
