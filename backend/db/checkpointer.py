"""LangGraph PostgreSQL checkpointer helpers."""

from __future__ import annotations

import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import logger

from .pool import get_async_pool

_postgres_checkpointer = None
_postgres_setup_completed = False
_postgres_lock: asyncio.Lock | None = None


async def get_postgres_checkpointer() -> AsyncPostgresSaver:
    """Get or create the shared PostgreSQL LangGraph checkpointer."""

    global _postgres_checkpointer, _postgres_setup_completed, _postgres_lock

    if _postgres_checkpointer is not None:
        return _postgres_checkpointer

    if _postgres_lock is None:
        _postgres_lock = asyncio.Lock()

    async with _postgres_lock:
        if _postgres_checkpointer is not None:
            return _postgres_checkpointer

        pool = await get_async_pool()
        checkpointer = AsyncPostgresSaver(pool)

        if not _postgres_setup_completed:
            await checkpointer.setup()
            _postgres_setup_completed = True

        _postgres_checkpointer = checkpointer
        logger.info("PostgreSQL checkpointer initialized")
        return _postgres_checkpointer


async def close_postgres_checkpointer() -> None:
    """Reset the cached checkpointer so a new pool can be created cleanly."""

    global _postgres_checkpointer, _postgres_setup_completed
    _postgres_checkpointer = None
    _postgres_setup_completed = False
