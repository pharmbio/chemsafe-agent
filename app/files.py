from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from app.downloads import is_data_path
from app.state import FileRecord, UIState
from backend.utils.output_paths import list_task_files, remove_task_dir
from backend.utils.storage_paths import thread_data_root


def sanitize_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in (" ", ".", "_", "-")).strip() or "file"


def hash_file(path: Path) -> str:
    hasher = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def list_upload_files(thread_id: str, *, user_id: Optional[str]) -> List[Path]:
    root = thread_data_root(thread_id, user_id=user_id, create=False)
    if not root.exists():
        return []
    files = [path for path in root.rglob("*") if path.is_file()]
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return files


def scan_thread_files(thread_id: str, *, user_id: Optional[str]) -> List[FileRecord]:
    """Every file this thread owns — uploads and outputs — newest first."""
    combined: List[Path] = []
    seen: set[Path] = set()
    for path in list_upload_files(thread_id, user_id=user_id) + list_task_files(thread_id, user_id=user_id):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        combined.append(path)

    records: List[FileRecord] = []
    for path in combined:
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            # The agent may delete or replace a file between listing and stat.
            continue
        records.append(
            FileRecord(
                path=str(path),
                hash=None,
                name=path.name,
                uploaded_at=modified_at,
                record_id=None,
            )
        )
    return records


def save_uploaded_file(
    uploaded_file,
    *,
    user_id: Optional[str],
    thread_id: Optional[str],
) -> Tuple[Path, str]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    original_name = getattr(uploaded_file, "orig_name", None) or os.path.basename(uploaded_file.name)
    filename, ext = os.path.splitext(original_name)
    safe_name = sanitize_filename(filename)
    destination_root = thread_data_root(thread_id, user_id=user_id, create=True)
    destination = destination_root / f"{safe_name}_{timestamp}{ext}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(uploaded_file.name, destination)
    return destination, hash_file(destination)


def delete_thread_data(user_id: Optional[str], thread_id: Optional[str]) -> None:
    if not user_id or not thread_id:
        return
    shutil.rmtree(thread_data_root(thread_id, user_id=user_id, create=False), ignore_errors=True)
    remove_task_dir(thread_id, user_id=user_id)


def clear_thread_uploads(user_id: str, thread_id: str) -> None:
    for path in list_upload_files(thread_id, user_id=user_id):
        path.unlink(missing_ok=True)
    shutil.rmtree(thread_data_root(thread_id, user_id=user_id, create=False), ignore_errors=True)


def refresh_thread_files(state: UIState, thread_id: Optional[str]) -> bool:
    """Rescan one thread's files. Returns True when the list changed.

    Only the active thread is ever scanned. The sidebar renders a file list for
    the active card alone — clicking any other card activates that thread, so a
    collapsed card's list is never read — which keeps this off the O(threads)
    path it used to sit on.
    """
    if not thread_id or not state.user_id:
        return False
    previous = list(state.thread_files.get(thread_id, []))
    current = scan_thread_files(thread_id, user_id=state.user_id)
    state.thread_files[thread_id] = current
    if thread_id == state.current_thread_id:
        state.uploaded_files = [record for record in current if is_data_path(record.path)]
    return current != previous
