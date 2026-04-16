"""Database access helpers for authentication."""

from __future__ import annotations

import asyncio
import re
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from psycopg.rows import dict_row

from app.config import logger
from backend.db import get_async_pool

_schema_ready = False
_schema_lock: asyncio.Lock | None = None
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _validate_email(email: str) -> str:
    normalized = _normalize_email(email)
    if not _EMAIL_RE.match(normalized):
        raise ValueError("Enter a valid email address")
    return normalized


@dataclass
class UserRecord:
    id: UUID
    email: str
    password_hash: str
    is_verified: bool
    last_login: Optional[datetime]


class AuthRepository:
    """Execute auth-related queries using the shared pool."""

    async def _ensure_schema(self) -> None:
        global _schema_ready, _schema_lock

        if _schema_ready:
            return

        if _schema_lock is None:
            _schema_lock = asyncio.Lock()

        async with _schema_lock:
            if _schema_ready:
                return

            pool = await get_async_pool()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS users (
                            id UUID PRIMARY KEY,
                            email TEXT UNIQUE NOT NULL,
                            password_hash TEXT NOT NULL,
                            is_verified BOOLEAN NOT NULL DEFAULT FALSE,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            last_login TIMESTAMPTZ
                        )
                        """
                    )
                    await cur.execute(
                        """
                        ALTER TABLE users
                        ALTER COLUMN is_verified SET DEFAULT FALSE
                        """
                    )
                    await cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS auth_sessions (
                            id UUID PRIMARY KEY,
                            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            session_token TEXT NOT NULL UNIQUE,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            expires_at TIMESTAMPTZ NOT NULL,
                            revoked_at TIMESTAMPTZ
                        )
                        """
                    )
                    await cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS auth_sessions_user_idx
                        ON auth_sessions (user_id)
                        WHERE revoked_at IS NULL
                        """
                    )
                    await cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS user_threads (
                            id UUID PRIMARY KEY,
                            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            thread_id TEXT NOT NULL UNIQUE,
                            title TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            ui_timeline JSONB NOT NULL DEFAULT '[]'::jsonb
                        )
                        """
                    )
                    await cur.execute(
                        """
                        ALTER TABLE user_threads
                        ADD COLUMN IF NOT EXISTS ui_timeline JSONB NOT NULL DEFAULT '[]'::jsonb
                        """
                    )
                    await cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS user_threads_user_idx
                        ON user_threads (user_id, updated_at DESC)
                        """
                    )

            _schema_ready = True

    async def ensure_schema(self) -> None:
        await self._ensure_schema()

    async def create_user(self, email: str, password_hash: str) -> UserRecord:
        await self._ensure_schema()
        pool = await get_async_pool()
        normalized = _validate_email(email)
        user_id = uuid4()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    INSERT INTO users (id, email, password_hash, is_verified)
                    VALUES (%s, %s, %s, FALSE)
                    RETURNING id, email, password_hash, is_verified, last_login
                    """,
                    (user_id, normalized, password_hash),
                )
                row = await cur.fetchone()
        return UserRecord(**row)

    async def get_user_by_email(self, email: str) -> Optional[UserRecord]:
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id, email, password_hash, is_verified, last_login
                    FROM users
                    WHERE email = %s
                    LIMIT 1
                    """,
                    (_validate_email(email),),
                )
                row = await cur.fetchone()
        return UserRecord(**row) if row else None

    async def get_user_by_id(self, user_id: UUID) -> Optional[UserRecord]:
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id, email, password_hash, is_verified, last_login
                    FROM users
                    WHERE id = %s
                    """,
                    (user_id,),
                )
                row = await cur.fetchone()
        return UserRecord(**row) if row else None

    async def record_login(self, user_id: UUID) -> None:
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE users
                    SET last_login = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (user_id,),
                )

    async def create_session(self, *, user_id: UUID, session_token: str, expires_at: datetime) -> UUID:
        await self._ensure_schema()
        pool = await get_async_pool()
        session_id = uuid4()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    INSERT INTO auth_sessions (id, user_id, session_token, expires_at)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (session_id, user_id, session_token, expires_at),
                )
                row = await cur.fetchone()
        return row["id"] if row else session_id

    async def get_session(self, session_token: str):
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT *
                    FROM auth_sessions
                    WHERE session_token = %s
                      AND revoked_at IS NULL
                      AND expires_at > NOW()
                    LIMIT 1
                    """,
                    (session_token,),
                )
                return await cur.fetchone()

    async def revoke_session(self, session_token: str) -> None:
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = NOW()
                    WHERE session_token = %s
                    """,
                    (session_token,),
                )

    async def revoke_user_sessions(self, user_id: UUID) -> None:
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = NOW()
                    WHERE user_id = %s AND revoked_at IS NULL
                    """,
                    (user_id,),
                )

    async def upsert_thread(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        title: str,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
    ) -> None:
        await self._ensure_schema()
        pool = await get_async_pool()
        thread_row_id = uuid4()
        created = created_at or datetime.now(timezone.utc)
        updated = updated_at or created
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO user_threads (id, user_id, thread_id, title, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (thread_id) DO UPDATE
                    SET title = EXCLUDED.title,
                        updated_at = GREATEST(user_threads.updated_at, EXCLUDED.updated_at)
                    WHERE user_threads.user_id = %s
                    """,
                    (thread_row_id, user_id, thread_id, title, created, updated, user_id),
                )

    async def list_threads(self, user_id: UUID) -> list[dict[str, Any]]:
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT thread_id, user_id, title, created_at, updated_at, ui_timeline
                    FROM user_threads
                    WHERE user_id = %s
                    ORDER BY updated_at DESC
                    """,
                    (user_id,),
                )
                return await cur.fetchall()

    async def get_thread(self, user_id: UUID, thread_id: str) -> Optional[dict[str, Any]]:
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT thread_id, user_id, title, created_at, updated_at, ui_timeline
                    FROM user_threads
                    WHERE user_id = %s AND thread_id = %s
                    LIMIT 1
                    """,
                    (user_id, thread_id),
                )
                return await cur.fetchone()

    async def update_thread_title(self, user_id: UUID, thread_id: str, title: str) -> None:
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE user_threads
                    SET title = %s,
                        updated_at = NOW()
                    WHERE user_id = %s AND thread_id = %s
                    """,
                    (title, user_id, thread_id),
                )

    async def get_thread_timeline(self, user_id: UUID, thread_id: str) -> Any:
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT ui_timeline
                    FROM user_threads
                    WHERE user_id = %s AND thread_id = %s
                    LIMIT 1
                    """,
                    (user_id, thread_id),
                )
                row = await cur.fetchone()
        return row.get("ui_timeline") if row else []

    async def update_thread_timeline(
        self,
        user_id: UUID,
        thread_id: str,
        timeline: Any,
    ) -> None:
        await self._ensure_schema()
        pool = await get_async_pool()
        payload = json.dumps(timeline or [], ensure_ascii=True)
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE user_threads
                    SET ui_timeline = %s::jsonb,
                        updated_at = NOW()
                    WHERE user_id = %s AND thread_id = %s
                    """,
                    (payload, user_id, thread_id),
                )

    async def delete_thread(self, user_id: UUID, thread_id: str) -> None:
        await self._ensure_schema()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM user_threads
                    WHERE user_id = %s AND thread_id = %s
                    """,
                    (user_id, thread_id),
                )
