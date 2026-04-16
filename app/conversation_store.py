from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from psycopg.rows import dict_row

from app.config import DEFAULT_CONVERSATION_TITLE
from app.state import ConversationMeta
from backend.auth.repository import AuthRepository
from backend.db import get_async_pool

_repo = AuthRepository()
_legacy_table_exists: bool | None = None
_legacy_table_lock: asyncio.Lock | None = None
_legacy_migration_lock: asyncio.Lock | None = None
_legacy_users_migrated: set[str] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _to_uuid(user_id: str) -> UUID:
    return UUID(str(user_id))


def _row_to_meta(row: dict) -> ConversationMeta:
    return ConversationMeta(
        thread_id=row["thread_id"],
        title=row.get("title") or DEFAULT_CONVERSATION_TITLE,
        created_at=_to_iso(row["created_at"]),
        updated_at=_to_iso(row["updated_at"]),
        user_id=str(row["user_id"]),
    )


def _make_thread_id(user_id: str) -> str:
    return f"{user_id}:{uuid4()}"


async def _ensure_schema() -> None:
    await _repo.ensure_schema()


async def _has_legacy_conversation_table() -> bool:
    global _legacy_table_exists, _legacy_table_lock

    if _legacy_table_exists is not None:
        return _legacy_table_exists

    if _legacy_table_lock is None:
        _legacy_table_lock = asyncio.Lock()

    async with _legacy_table_lock:
        if _legacy_table_exists is not None:
            return _legacy_table_exists

        pool = await get_async_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'ui_conversations'
                    ) AS present
                    """
                )
                row = await cur.fetchone()

        _legacy_table_exists = bool(row and row.get("present"))
        return _legacy_table_exists


async def _load_legacy_rows(user_id: str) -> list[dict]:
    if not await _has_legacy_conversation_table():
        return []

    pool = await get_async_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT thread_id, user_id, title, created_at, updated_at, messages
                FROM ui_conversations
                WHERE user_id = %s
                ORDER BY updated_at DESC
                """,
                (user_id,),
            )
            return await cur.fetchall()


async def _migrate_legacy_threads(user_id: str) -> None:
    global _legacy_migration_lock

    if user_id in _legacy_users_migrated:
        return

    if _legacy_migration_lock is None:
        _legacy_migration_lock = asyncio.Lock()

    async with _legacy_migration_lock:
        if user_id in _legacy_users_migrated:
            return

        await _ensure_schema()
        user_uuid = _to_uuid(user_id)
        legacy_rows = await _load_legacy_rows(user_id)

        for row in legacy_rows:
            await _repo.upsert_thread(
                user_id=user_uuid,
                thread_id=row["thread_id"],
                title=(row.get("title") or DEFAULT_CONVERSATION_TITLE).strip() or DEFAULT_CONVERSATION_TITLE,
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
            )
            legacy_timeline = row.get("messages")
            if isinstance(legacy_timeline, list):
                current_timeline = await _repo.get_thread_timeline(user_uuid, row["thread_id"])
                if not current_timeline:
                    await _repo.update_thread_timeline(user_uuid, row["thread_id"], legacy_timeline)

        _legacy_users_migrated.add(user_id)


async def load_threads(user_id: str) -> List[ConversationMeta]:
    await _ensure_schema()
    await _migrate_legacy_threads(user_id)
    rows = await _repo.list_threads(_to_uuid(user_id))
    return [_row_to_meta(row) for row in rows]


async def save_threads(user_id: str, threads: List[ConversationMeta]) -> None:
    await _ensure_schema()
    user_uuid = _to_uuid(user_id)
    for meta in threads:
        if meta.user_id != user_id:
            continue
        await _repo.upsert_thread(
            user_id=user_uuid,
            thread_id=meta.thread_id,
            title=meta.title.strip() or DEFAULT_CONVERSATION_TITLE,
            created_at=datetime.fromisoformat(meta.created_at),
            updated_at=datetime.fromisoformat(meta.updated_at),
        )


async def create_thread(user_id: str, title: Optional[str] = None) -> ConversationMeta:
    await _ensure_schema()
    resolved_title = (title or DEFAULT_CONVERSATION_TITLE).strip() or DEFAULT_CONVERSATION_TITLE
    timestamp = _now()
    thread_id = _make_thread_id(user_id)
    await _repo.upsert_thread(
        user_id=_to_uuid(user_id),
        thread_id=thread_id,
        title=resolved_title,
        created_at=timestamp,
        updated_at=timestamp,
    )
    return ConversationMeta(
        thread_id=thread_id,
        title=resolved_title,
        created_at=timestamp.isoformat(),
        updated_at=timestamp.isoformat(),
        user_id=user_id,
    )


async def upsert_thread(user_id: str, meta: ConversationMeta) -> None:
    await _ensure_schema()
    await _repo.upsert_thread(
        user_id=_to_uuid(user_id),
        thread_id=meta.thread_id,
        title=meta.title.strip() or DEFAULT_CONVERSATION_TITLE,
        created_at=datetime.fromisoformat(meta.created_at),
        updated_at=datetime.fromisoformat(meta.updated_at),
    )


async def update_thread_title(user_id: str, thread_id: str, title: str) -> None:
    await _ensure_schema()
    await _repo.update_thread_title(
        _to_uuid(user_id),
        thread_id,
        title.strip() or DEFAULT_CONVERSATION_TITLE,
    )


async def delete_thread(user_id: str, thread_id: str) -> None:
    await _ensure_schema()
    await _repo.delete_thread(_to_uuid(user_id), thread_id)


async def load_timeline(user_id: str, thread_id: str):
    await _ensure_schema()
    await _migrate_legacy_threads(user_id)
    return await _repo.get_thread_timeline(_to_uuid(user_id), thread_id)


async def save_timeline(user_id: str, thread_id: str, timeline) -> None:
    await _ensure_schema()
    user_uuid = _to_uuid(user_id)
    existing = await _repo.get_thread(user_uuid, thread_id)
    if not existing:
        timestamp = _now()
        await _repo.upsert_thread(
            user_id=user_uuid,
            thread_id=thread_id,
            title=DEFAULT_CONVERSATION_TITLE,
            created_at=timestamp,
            updated_at=timestamp,
        )
    await _repo.update_thread_timeline(user_uuid, thread_id, timeline if timeline is not None else [])


async def load_messages(user_id: str, thread_id: str) -> list[dict]:
    payload = await load_timeline(user_id, thread_id)
    return payload if isinstance(payload, list) else []


async def save_messages(user_id: str, thread_id: str, messages: list[dict]) -> None:
    await save_timeline(user_id, thread_id, messages or [])
