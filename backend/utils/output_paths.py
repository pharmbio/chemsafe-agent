from __future__ import annotations

import contextvars
import shutil
import time
from pathlib import Path
from typing import Optional

from app.config import RESULTS_ROOT
from backend.utils.storage_paths import thread_folder_name


_conversation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "chemsafe_conversation_id",
    default=None,
)
_user_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "chemsafe_user_id",
    default=None,
)


def get_results_root() -> Path:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    return RESULTS_ROOT


def set_current_conversation_id(conversation_id: Optional[str]):
    if conversation_id is None:
        return None
    return _conversation_id_var.set(conversation_id)


def reset_current_conversation_id(token) -> None:
    if token is None:
        _conversation_id_var.set(None)
        return
    try:
        _conversation_id_var.reset(token)
    except ValueError:
        _conversation_id_var.set(None)


def get_current_conversation_id() -> Optional[str]:
    return _conversation_id_var.get()


def set_current_user_id(user_id: Optional[str]):
    if user_id is None:
        return None
    return _user_id_var.set(user_id)


def reset_current_user_id(token) -> None:
    if token is None:
        _user_id_var.set(None)
        return
    try:
        _user_id_var.reset(token)
    except ValueError:
        _user_id_var.set(None)


def get_current_user_id() -> Optional[str]:
    return _user_id_var.get()


def user_output_root(user_id: Optional[str] = None) -> Path:
    resolved_user_id = user_id or get_current_user_id() or "anonymous-user"
    path = get_results_root() / resolved_user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _conversation_candidate_dirs(
    conversation_id: Optional[str],
    *,
    user_id: Optional[str] = None,
) -> list[Path]:
    resolved_user_id = user_id or get_current_user_id() or "anonymous-user"
    resolved_conversation_id = conversation_id or get_current_conversation_id() or "default-thread"
    user_root = user_output_root(resolved_user_id)
    folder_name = thread_folder_name(resolved_conversation_id, resolved_user_id)
    candidates = [user_root / folder_name]
    if folder_name != resolved_conversation_id:
        candidates.append(user_root / resolved_conversation_id)
    return candidates


def conversation_output_root(
    conversation_id: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
) -> Path:
    candidates = _conversation_candidate_dirs(conversation_id, user_id=user_id)
    path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_output_folder(
    preferred_folder: Optional[str] = None,
    *,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Path:
    base_dir = conversation_output_root(conversation_id, user_id=user_id)
    if not preferred_folder:
        return base_dir

    candidate = Path(preferred_folder)
    if not candidate.is_absolute():
        candidate = base_dir / candidate

    try:
        candidate.resolve().relative_to(base_dir.resolve())
    except ValueError:
        return base_dir

    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def task_file_path(
    filename: str,
    *,
    output_folder: Optional[str | Path] = None,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Path:
    if isinstance(output_folder, Path):
        folder = output_folder
    else:
        folder = resolve_output_folder(
            str(output_folder) if output_folder else None,
            conversation_id=conversation_id,
            user_id=user_id,
        )
    folder.mkdir(parents=True, exist_ok=True)
    return folder / filename


def list_task_files(
    conversation_id: str,
    *,
    user_id: Optional[str] = None,
) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for directory in _conversation_candidate_dirs(conversation_id, user_id=user_id):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(path)
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files


def remove_task_dir(
    conversation_id: str,
    *,
    user_id: Optional[str] = None,
) -> None:
    for directory in _conversation_candidate_dirs(conversation_id, user_id=user_id):
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)


def describe_output_scope(
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> str:
    resolved_user_id = user_id or get_current_user_id() or "anonymous-user"
    resolved_conversation_id = conversation_id or get_current_conversation_id() or "default-thread"
    root = conversation_output_root(resolved_conversation_id, user_id=resolved_user_id)
    return (
        "Active output scope:\n"
        f"- user_id: {resolved_user_id}\n"
        f"- conversation_id: {resolved_conversation_id}\n"
        f"- output_root: {root}\n"
        "Write all generated files under this directory or its subdirectories only.\n"
        "In python_executor, use the injected output helpers tied to this scope."
    )


_ARTIFACT_CACHE_TTL_SECONDS = 2.0
_artifact_cache: dict[tuple, tuple[float, str]] = {}


def describe_output_artifacts(
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    max_items: int = 25,
) -> str:
    """List the files this conversation has already produced, newest first.

    Read from disk rather than from a model-maintained list, so a follow-up turn
    can refer to earlier artifacts even when the narrative summary lost them.
    Returns an empty string when nothing has been produced yet.

    Briefly cached: this runs before every model call, and a run that writes
    many files would otherwise re-walk and re-stat the whole output tree each
    time. The TTL is short enough that a file written mid-run still shows up.
    """
    resolved_user_id = user_id or get_current_user_id() or "anonymous-user"
    resolved_conversation_id = conversation_id or get_current_conversation_id() or "default-thread"

    cache_key = (resolved_user_id, resolved_conversation_id, max_items)
    cached = _artifact_cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _ARTIFACT_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        root = conversation_output_root(resolved_conversation_id, user_id=resolved_user_id)
        entries = [path for path in root.rglob("*") if path.is_file()]
    except OSError:
        _artifact_cache[cache_key] = (now, "")
        return ""
    if not entries:
        _artifact_cache[cache_key] = (now, "")
        return ""

    def _sort_key(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    entries.sort(key=_sort_key, reverse=True)
    truncated = len(entries) - max_items
    lines = []
    for path in entries[:max_items]:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        lines.append(f"- {path} ({size:,} bytes)")
    if truncated > 0:
        lines.append(f"- ... and {truncated} more file(s) in this output scope")

    rendered = "\n".join(lines)
    _artifact_cache[cache_key] = (now, rendered)
    return rendered
