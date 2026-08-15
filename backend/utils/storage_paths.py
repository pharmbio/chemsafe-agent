from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.config import DATA_ROOT, MEMORY_ROOT, RESULTS_ROOT


@lru_cache(maxsize=1)
def get_data_root() -> Path:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    return DATA_ROOT


@lru_cache(maxsize=1)
def get_memory_root() -> Path:
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    return MEMORY_ROOT


@lru_cache(maxsize=1)
def get_results_root() -> Path:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    return RESULTS_ROOT


def thread_folder_name(thread_id: Optional[str], user_id: Optional[str]) -> str:
    """Normalize a thread id into a filesystem-friendly per-user folder name."""
    if not thread_id:
        return "unassigned"
    if user_id and thread_id.startswith(f"{user_id}:"):
        suffix = thread_id.split(":", 1)[1]
        return suffix or thread_id
    return thread_id


def user_data_root(user_id: Optional[str] = None) -> Path:
    resolved_user_id = user_id or "anonymous-user"
    path = get_data_root() / resolved_user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def thread_data_root(
    thread_id: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
    create: bool = True,
) -> Path:
    resolved_thread_id = thread_id or "unassigned"
    user_root = user_data_root(user_id)
    folder_name = thread_folder_name(resolved_thread_id, user_id)
    canonical = user_root / folder_name
    legacy = user_root / resolved_thread_id

    path = canonical
    if folder_name != resolved_thread_id and not canonical.exists() and legacy.exists():
        path = legacy

    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path
