from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import shutil
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr
import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from gradio.themes.utils import colors

from app.app_config import AppRunConfig
from app.config import (
    APP_DESCRIPTION,
    APP_TITLE,
    DATA_ROOT,
    DEFAULT_CONVERSATION_TITLE,
    GRADIO_SERVER_NAME,
    GRADIO_SERVER_PORT,
    RESULTS_ROOT,
)
from app.conversation_store import (
    create_thread,
    delete_thread,
    load_threads,
    load_timeline,
    save_timeline,
    update_thread_title,
)
from app.langgraph_runner import build_stream_input, stream_langgraph_events
from app.partners import get_partner_organizations
from app.state import FileRecord, UIState
from app.ui.chat_timeline import (
    append_user_message,
    export_timeline_snapshot,
    process_ai_message,
    process_chunk,
    process_tool_call_start,
    process_tool_result,
    rebuild_from_plain_messages,
    rebuild_from_timeline_snapshot,
    reset_chat_messages,
)
from backend.auth.service import AuthService
from backend.db import (
    close_async_pool,
    close_postgres_checkpointer,
    get_async_pool,
    get_postgres_checkpointer,
)
from backend.utils.output_paths import conversation_output_root, list_task_files, remove_task_dir
from backend.utils.storage_paths import thread_data_root

AUTH_SERVICE = AuthService()
FILES_ROUTER = APIRouter(prefix="/api/files")

PASSWORD_MIN_LENGTH = 8
MAX_VISIBLE_FILES = 100
FILE_LIST_REFRESH_INTERVAL_SECONDS = 1.0
DOWNLOAD_ROUTE = "/api/files/download"
ALLOWED_DOWNLOAD_ROOTS = (Path(DATA_ROOT).resolve(), Path(RESULTS_ROOT).resolve())
_DOWNLOAD_SECRET = b"chemsafe-download"
LOGO_PATH = "images/logo.png"
INTRO_IMAGE_PATH = "images/agent_illustration.png"
INTRO_IMAGE_ALT = f"{APP_TITLE} illustration"
HEADER_LINKS_HTML = (
    "<div class='header-links-content'>"
    "<a class='header-link' href='https://github.com/pharmbio/chemsafe-agent' target='_blank' rel='noopener noreferrer'>GitHub</a>"
    "<span class='header-link-divider' aria-hidden='true'>|</span>"
    "<a class='header-link' href='/' target='_self' rel='noopener noreferrer'>Workspace</a>"
    "</div>"
)

PRIMARY_FERN = colors.Color(
    c50="#ebf6ff",
    c100="#d6ebfb",
    c200="#b4d8f1",
    c300="#8ec3e4",
    c400="#5fa8d4",
    c500="#2f88c1",
    c600="#025e8d",
    c700="#01486d",
    c800="#01334e",
    c900="#001f33",
    c950="#00111d",
    name="chemsafe_primary_blue",
)

SECONDARY_SAGE = colors.Color(
    c50="#f8f8f8",
    c100="#f1f1f1",
    c200="#e6e6e6",
    c300="#d5d5d5",
    c400="#bcbcbc",
    c500="#8d8d8d",
    c600="#666666",
    c700="#4a4a4a",
    c800="#2f2f2f",
    c900="#1f1f1f",
    c950="#121212",
    name="chemsafe_secondary_neutral",
)

CHEMSAFE_THEME = (
    gr.themes.Default(
        primary_hue=PRIMARY_FERN,
        secondary_hue=SECONDARY_SAGE,
        neutral_hue=colors.gray,
    ).set(
        color_accent="*primary_600",
        color_accent_soft="#ebf6ff",
        color_accent_soft_dark="*primary_700",
        button_primary_background_fill="*primary_600",
        button_primary_background_fill_hover="*primary_500",
        button_primary_text_color="#ffffff",
        button_primary_text_color_hover="#ffffff",
    )
)


def _validate_password_strength(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters long")
    if password.isdigit() or password.isalpha():
        raise ValueError("Password must include both letters and numbers")


def _inline_image_src(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    mime, _ = mimetypes.guess_type(str(path))
    return f"data:{mime or 'image/png'};base64,{data}"


def _logo_html() -> str:
    logo_src = _inline_image_src(Path(LOGO_PATH))
    if not logo_src:
        return ""
    return f'<img src="{logo_src}" alt="{APP_TITLE} logo" class="app-logo-img" />'


def _intro_markdown() -> str:
    image_src = _inline_image_src(Path(INTRO_IMAGE_PATH))
    if not image_src:
        return APP_DESCRIPTION
    return f"![{INTRO_IMAGE_ALT}]({image_src})"


def _partner_logos_html() -> str:
    cards: List[str] = []
    for org in get_partner_organizations():
        logo_src = _inline_image_src(Path(org["logo"]))
        if not logo_src:
            continue
        size = (org.get("size") or "").lower()
        extra_class = " partner-logo-card--xl" if size == "xl" else ""
        cards.append(
            (
                "<a class='partner-logo-card{extra}' href='{href}' target='_blank' "
                "rel='noopener noreferrer' title='{title}'>"
                "<img src='{src}' alt='{alt}' />"
                "</a>"
            ).format(
                extra=extra_class,
                href=escape(org["url"], quote=True),
                title=escape(org["name"], quote=True),
                src=escape(logo_src, quote=True),
                alt=escape(f"{org['name']} logo", quote=True),
            )
        )
    if not cards:
        return ""
    return (
        "<div class='partner-slider' data-partner-slider='1'>"
        "<div class='partner-slider__viewport'>"
        "<div class='partner-slider__track'>{cards}</div>"
        "</div>"
        "<div class='partner-slider__dots' role='tablist' aria-label='Partner carousel controls'></div>"
        "</div>"
    ).format(cards="".join(cards))


INTRO_MARKDOWN = _intro_markdown()


def _thread_to_dict(meta) -> Dict[str, Any]:
    return {
        "thread_id": meta.thread_id,
        "title": meta.title,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "user_id": meta.user_id,
    }


def _auth_message(text: str, success: bool = True) -> str:
    prefix = "**Success:**" if success else "**Error:**"
    return f"{prefix} {text}"


def _auth_status_text(state: UIState) -> str:
    if not state.is_authenticated:
        base = " "
    else:
        base = f"**Signed in as **: signed in as `{state.user_email}`."
    if state.auth_error:
        return f"{base}\n\n{state.auth_error}"
    return base


def _logout_visibility(state: Optional[UIState]):
    return gr.update(visible=bool(state and state.is_authenticated))


def _login_visibility(state: Optional[UIState]):
    return gr.update(visible=not (state and state.is_authenticated))


def _new_task_button_update(state: Optional[UIState]):
    return gr.update(interactive=bool(state and state.is_authenticated and state.is_verified))


def _initialize_state() -> UIState:
    return UIState()


def _reset_user_state(state: UIState) -> None:
    state.user_id = None
    state.user_email = None
    state.is_authenticated = False
    state.is_verified = False
    state.auth_error = None
    state.pending_reset_token = None
    state.session_token = None
    state.thread_ids = []
    state.current_thread_id = None
    state.selected_thread_id = None
    state.thread_files.clear()
    state.uploaded_files = []
    state.current_app_config = None
    state.stop_signals = {}
    state.running_threads = set()
    state.waiting_for_approval = False
    state.approval_interrupted = False
    state.stale_threads = set()
    state.pending_stream_events = {}
    state.last_run_at = {}
    state.processed_message_ids = set()
    state.processed_tools_ids = set()
    state.processed_content_hashes = set()
    reset_chat_messages(state)


def _parse_complete_payload(payload: Any) -> Tuple[bool, Optional[datetime]]:
    if isinstance(payload, dict):
        interrupted = bool(payload.get("interrupted"))
        completed_at = payload.get("completed_at")
    else:
        interrupted = bool(payload)
        completed_at = None

    completed_dt = None
    if isinstance(completed_at, (int, float)):
        completed_dt = datetime.fromtimestamp(completed_at, tz=timezone.utc)
    elif isinstance(completed_at, str):
        try:
            completed_dt = datetime.fromisoformat(completed_at)
        except ValueError:
            completed_dt = None
    return interrupted, completed_dt


def _apply_stream_event(event_type: str, payload: Any, state: UIState) -> bool:
    if event_type == "ai_message" and isinstance(payload, dict):
        return process_ai_message(state, payload.get("agent"), payload.get("message"), payload.get("tool_calls"))
    if event_type == "tool_call_start" and isinstance(payload, dict):
        return process_tool_call_start(state, payload.get("agent"), payload.get("call"))
    if event_type == "tool_result" and isinstance(payload, dict):
        return process_tool_result(state, payload.get("agent"), payload.get("call_id"), payload.get("result"))
    if event_type == "chunk":
        return process_chunk(state, payload)
    if event_type == "complete":
        interrupted, _ = _parse_complete_payload(payload)
        state.waiting_for_approval = interrupted
        state.approval_interrupted = interrupted
        return True
    return False


def _record_stream_error(state: UIState, exc: Exception) -> bool:
    state.waiting_for_approval = False
    state.approval_interrupted = False
    error_text = (
        "The current run stopped because a tool raised an unhandled error.\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        "You can send another message to correct the issue and continue."
    )
    return process_ai_message(
        state,
        "assistant",
        SimpleNamespace(content=error_text, id=state.next_message_id("assistant_error")),
        None,
    )


def _drain_pending_stream_events(state: UIState, thread_id: Optional[str]) -> bool:
    if not thread_id:
        return False
    buffer = state.pending_stream_events.get(thread_id)
    if not buffer:
        return False
    updated = False
    for event_type, payload in list(buffer):
        updated |= _apply_stream_event(event_type, payload, state)
    buffer.clear()
    state.stale_threads.discard(thread_id)
    return updated


def _clear_pending_chunk_events(state: UIState, thread_id: Optional[str]) -> None:
    if not thread_id:
        return
    buffer = state.pending_stream_events.get(thread_id)
    if not buffer:
        return
    retained = [(event_type, payload) for event_type, payload in buffer if event_type != "chunk"]
    if retained:
        state.pending_stream_events[thread_id] = retained
    else:
        state.pending_stream_events.pop(thread_id, None)


def _sanitize_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in (" ", ".", "_", "-")).strip() or "file"


def _hash_file(path: Path) -> str:
    hasher = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_resolve(path_value: str) -> Path:
    return Path(path_value).expanduser().resolve()


def _is_allowed_download_path(path: Path) -> bool:
    for root in ALLOWED_DOWNLOAD_ROOTS:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _is_data_path(path_value: str) -> bool:
    try:
        _safe_resolve(path_value).relative_to(Path(DATA_ROOT).resolve())
        return True
    except ValueError:
        return False


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _encode_download_token(payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_DOWNLOAD_SECRET, body, hashlib.sha256).digest()
    return f"{_urlsafe_b64encode(body)}.{_urlsafe_b64encode(signature)}"


def _decode_download_token(token: str) -> Dict[str, Any]:
    try:
        body_part, sig_part = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed download token") from exc
    body = _urlsafe_b64decode(body_part)
    provided_sig = _urlsafe_b64decode(sig_part)
    expected_sig = hmac.new(_DOWNLOAD_SECRET, body, hashlib.sha256).digest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise HTTPException(status_code=403, detail="Invalid download token")
    payload = json.loads(body.decode("utf-8"))
    expires_at = int(payload.get("exp", 0))
    if not expires_at or expires_at < int(time.time()):
        raise HTTPException(status_code=401, detail="Download link expired")
    return payload


async def _validate_download_access(payload: Dict[str, Any], resolved_path: Path) -> None:
    user_id = payload.get("user_id")
    session_token = payload.get("session_token")
    thread_id = payload.get("thread_id")
    if not user_id or not session_token or not thread_id:
        raise HTTPException(status_code=403, detail="Access denied")

    restored_user = await AUTH_SERVICE.restore_session(session_token)
    if not restored_user or str(restored_user.id) != str(user_id):
        raise HTTPException(status_code=403, detail="Access denied")

    allowed_dirs = [
        thread_data_root(thread_id, user_id=user_id, create=False).resolve(),
        conversation_output_root(thread_id, user_id=user_id).resolve(),
    ]
    for directory in allowed_dirs:
        try:
            resolved_path.relative_to(directory)
            return
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail="Access denied")


def _build_download_payload(record: FileRecord, thread_id: str, *, user_id: Optional[str], session_token: Optional[str]):
    if not record.path or not user_id or not session_token:
        return None
    resolved_path = _safe_resolve(record.path)
    if not _is_allowed_download_path(resolved_path):
        return None
    issued_at = int(time.time())
    return {
        "path": str(resolved_path),
        "thread_id": thread_id,
        "name": record.name,
        "exp": issued_at + 600,
        "ts": issued_at,
        "user_id": user_id,
        "session_token": session_token,
    }


def _list_upload_files(thread_id: str, *, user_id: Optional[str]) -> List[Path]:
    root = thread_data_root(thread_id, user_id=user_id, create=False)
    if not root.exists():
        return []
    files = [path for path in root.rglob("*") if path.is_file()]
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return files


def _scan_thread_files(thread_id: str, *, user_id: Optional[str]) -> List[FileRecord]:
    combined: List[Path] = []
    seen: set[Path] = set()
    for path in _list_upload_files(thread_id, user_id=user_id) + list_task_files(thread_id, user_id=user_id):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        combined.append(path)

    records: List[FileRecord] = []
    for path in combined:
        records.append(
            FileRecord(
                path=str(path),
                hash=None,
                name=path.name,
                uploaded_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
                record_id=None,
            )
        )
    return records


def _save_uploaded_file(uploaded_file, *, user_id: Optional[str], thread_id: Optional[str]) -> Tuple[Path, str]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    original_name = getattr(uploaded_file, "orig_name", None) or os.path.basename(uploaded_file.name)
    filename, ext = os.path.splitext(original_name)
    safe_name = _sanitize_filename(filename)
    destination_root = thread_data_root(thread_id, user_id=user_id, create=True)
    destination = destination_root / f"{safe_name}_{timestamp}{ext}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(uploaded_file.name, destination)
    return destination, _hash_file(destination)


def _delete_thread_data(user_id: Optional[str], thread_id: Optional[str]) -> None:
    if not user_id or not thread_id:
        return
    shutil.rmtree(thread_data_root(thread_id, user_id=user_id, create=False), ignore_errors=True)
    remove_task_dir(thread_id, user_id=user_id)


async def _refresh_thread_files_for(state: UIState, thread_id: Optional[str]) -> bool:
    if not thread_id or not state.user_id:
        return False
    previous = list(state.thread_files.get(thread_id, []))
    current = _scan_thread_files(thread_id, user_id=state.user_id)
    state.thread_files[thread_id] = current
    if thread_id == state.current_thread_id:
        state.uploaded_files = [record for record in current if _is_data_path(record.path)]
    return current != previous


async def _persist_timeline_snapshot(thread_id: Optional[str], state: UIState) -> None:
    if not thread_id or not state.user_id:
        return
    await save_timeline(state.user_id, thread_id, export_timeline_snapshot(state))


async def _apply_event_to_persisted_timeline(state: UIState, thread_id: Optional[str], event_type: str, payload: Any) -> None:
    if not thread_id or not state.user_id or event_type in {"chunk", "complete"}:
        return
    snapshot = await load_timeline(state.user_id, thread_id)
    timeline_state = UIState()
    if isinstance(snapshot, dict):
        rebuild_from_timeline_snapshot(timeline_state, snapshot)
    updated = _apply_stream_event(event_type, payload, timeline_state)
    if updated:
        await save_timeline(state.user_id, thread_id, export_timeline_snapshot(timeline_state))


async def _refresh_conversation(state: UIState, thread_id: str) -> None:
    state.current_thread_id = thread_id
    state.stale_threads.discard(thread_id)
    state.processed_message_ids = set()
    state.processed_tools_ids = set()
    state.processed_content_hashes = set()
    snapshot = await load_timeline(state.user_id, thread_id) if state.user_id else None
    rebuilt = False
    if isinstance(snapshot, dict):
        rebuilt = rebuild_from_timeline_snapshot(state, snapshot)
    elif isinstance(snapshot, list):
        rebuild_from_plain_messages(state, snapshot)
        rebuilt = True
    if not rebuilt:
        reset_chat_messages(state)
    state.ensure_thread_storage(thread_id)
    await _refresh_thread_files_for(state, thread_id)
    state.uploaded_files = [record for record in state.thread_files.get(thread_id, []) if _is_data_path(record.path)]


async def _sync_user_threads(state: UIState, ensure_one: bool = True) -> None:
    if not state.user_id:
        state.thread_ids = []
        state.current_thread_id = None
        state.selected_thread_id = None
        state.thread_files = {}
        state.uploaded_files = []
        reset_chat_messages(state)
        return

    threads = await load_threads(state.user_id)
    if not threads and ensure_one:
        await create_thread(state.user_id, DEFAULT_CONVERSATION_TITLE)
        threads = await load_threads(state.user_id)

    state.thread_ids = [_thread_to_dict(meta) for meta in threads]
    valid_ids = {thread["thread_id"] for thread in state.thread_ids}
    for thread_id in valid_ids:
        state.ensure_thread_storage(thread_id)
        await _refresh_thread_files_for(state, thread_id)

    state.stale_threads = {thread_id for thread_id in state.stale_threads if thread_id in valid_ids}
    for thread_id in list(state.pending_stream_events):
        if thread_id not in valid_ids:
            state.pending_stream_events.pop(thread_id, None)

    if state.current_thread_id not in valid_ids:
        state.current_thread_id = state.thread_ids[0]["thread_id"] if state.thread_ids else None
    if state.selected_thread_id not in valid_ids:
        state.selected_thread_id = state.current_thread_id

    if state.current_thread_id:
        await _refresh_conversation(state, state.current_thread_id)
    else:
        reset_chat_messages(state)
        state.uploaded_files = []


def _render_thread_files(state: UIState, thread_id: str) -> str:
    files = state.thread_files.get(thread_id, [])
    if not files:
        return "<p class='conversation-card__empty'>No output files yet.</p>"
    items: List[str] = []
    for record in files[:MAX_VISIBLE_FILES]:
        payload = _build_download_payload(
            record,
            thread_id,
            user_id=state.user_id,
            session_token=state.session_token,
        )
        if payload:
            token = _encode_download_token(payload)
            href = f"{DOWNLOAD_ROUTE}?token={token}"
            name_markup = (
                "<a class='conversation-card__file-link' href='{href}' "
                "target='_blank' rel='noopener' data-download-link='{token}' "
                "data-file-name='{download_name}' download='{download_name}'>"
                "{label}</a>"
            ).format(
                href=escape(href, quote=True),
                token=escape(token, quote=True),
                download_name=escape(record.name, quote=True),
                label=escape(record.name),
            )
        else:
            name_markup = f"<span class='conversation-card__file-name'>{escape(record.name)}</span>"
        items.append(
            "<li class='conversation-card__file-item' title='{title}'>{name}</li>".format(
                title=escape(record.path),
                name=name_markup,
            )
        )
    more_indicator = ""
    if len(files) > MAX_VISIBLE_FILES:
        more_indicator = f"<li class='conversation-card__file-more'>+{len(files) - MAX_VISIBLE_FILES} more…</li>"
    return (
        "<div class='conversation-card__files-container'>"
        "<ul class='conversation-card__files'>{}</ul>{}</div>"
    ).format("".join(items), more_indicator)


def _conversation_panel_markup(state: UIState) -> str:
    cards: List[str] = [
        "<div class='conversation-list__container' id='conversation-list-root'>",
        "<div class='conversation-list__header'>Conversation</div>",
    ]
    if not state.thread_ids:
        empty_text = "No conversations yet." if state.is_authenticated else "Log in to create your own task."
        cards.append(f"<p class='conversation-card__empty'>{empty_text}</p></div>")
        return "\n".join(cards)

    for thread in state.thread_ids:
        thread_id = thread["thread_id"]
        is_active = thread_id == state.current_thread_id
        cards.append(
            "<details class='conversation-card {active}' data-thread-id='{tid}' {open_attr}>"
            "<summary>"
            "<div class='conversation-card__title-row'>"
            "<span class='conversation-card__chevron' aria-hidden='true'></span>"
            "<span class='conversation-card__title'>{title}</span>"
            "<button type='button' class='conversation-card__delete' "
            "data-delete-thread='{tid}' data-confirm-message='Delete this conversation?'>🗑️</button>"
            "</div>"
            "</summary>"
            "<div class='conversation-card__body'>{files}</div>"
            "</details>".format(
                active="is-active" if is_active else "",
                tid=escape(thread_id),
                open_attr="open" if is_active else "",
                title=escape(thread.get("title") or "Conversation"),
                files=_render_thread_files(state, thread_id),
            )
        )
    cards.append("</div>")
    return "\n".join(cards)


def _conversation_panel_update(state: UIState):
    return gr.update(value=_conversation_panel_markup(state))


async def on_app_load():
    state = _initialize_state()
    await _sync_user_threads(state, ensure_one=False)
    return (
        state,
        _auth_status_text(state),
        _logout_visibility(state),
        _conversation_panel_markup(state),
        list(state.messages),
        gr.update(value=""),
        gr.update(value=""),
        _login_visibility(state),
        _new_task_button_update(state),
    )


async def on_register(email: str, password: str, confirm: str, state: UIState):
    if state is None:
        state = _initialize_state()
    email = (email or "").strip()
    password = password or ""
    confirm = confirm or ""
    if not email or not password:
        return state, _auth_message("Email and password are required.", success=False), _logout_visibility(state), _login_visibility(state)
    if password != confirm:
        return state, _auth_message("Passwords do not match.", success=False), _logout_visibility(state), _login_visibility(state)
    try:
        _validate_password_strength(password)
        await AUTH_SERVICE.register_user(email, password)
        state.auth_error = _auth_message("Registration submitted. Approval pending. You will be notified by email once your account is ready.", success=True)
    except Exception as exc:
        state.auth_error = _auth_message(str(exc), success=False)
    return state, _auth_status_text(state), _logout_visibility(state), _login_visibility(state)


async def on_login(email: str, password: str, state: UIState):
    if state is None:
        state = _initialize_state()
    email = (email or "").strip()
    password = password or ""
    if not email or not password:
        state.auth_error = _auth_message("Email and password are required.", success=False)
        return state, _auth_status_text(state), _logout_visibility(state), _conversation_panel_update(state), list(state.messages), _login_visibility(state), _new_task_button_update(state)
    try:
        user = await AUTH_SERVICE.login(email, password)
        state.user_id = str(user.id)
        state.user_email = user.email
        state.is_authenticated = True
        state.is_verified = True
        state.session_token = await AUTH_SERVICE.create_session(user.id)
        state.auth_error = _auth_message("Signed in successfully.", success=True)
        await _sync_user_threads(state)
    except Exception as exc:
        _reset_user_state(state)
        state.auth_error = _auth_message(str(exc), success=False)
    return state, _auth_status_text(state), _logout_visibility(state), _conversation_panel_update(state), list(state.messages), _login_visibility(state), _new_task_button_update(state)


async def on_logout(state: UIState):
    if state is None:
        state = _initialize_state()
    await AUTH_SERVICE.logout(state.session_token)
    _reset_user_state(state)
    state.auth_error = _auth_message("Logged out.", success=True)
    return state, _auth_status_text(state), _logout_visibility(state), _conversation_panel_update(state), list(state.messages), _login_visibility(state), _new_task_button_update(state)


async def _activate_thread(thread_id: Optional[str], state: UIState):
    if not thread_id or thread_id not in {thread["thread_id"] for thread in state.thread_ids}:
        return state, _conversation_panel_update(state), list(state.messages), gr.update(value="")
    state.selected_thread_id = thread_id
    await _refresh_conversation(state, thread_id)
    _drain_pending_stream_events(state, thread_id)
    state.waiting_for_approval = False
    state.approval_interrupted = False
    state.current_app_config = None
    return state, _conversation_panel_update(state), list(state.messages), gr.update(value="")


async def on_new_task(state: UIState):
    if state is None:
        state = _initialize_state()
    if not state.user_id:
        state.auth_error = _auth_message("Please sign in first.", success=False)
        return state, _conversation_panel_update(state), list(state.messages), gr.update(value="")
    meta = await create_thread(state.user_id, DEFAULT_CONVERSATION_TITLE)
    state.current_thread_id = meta.thread_id
    state.selected_thread_id = meta.thread_id
    state.thread_ids.insert(0, _thread_to_dict(meta))
    state.thread_files[meta.thread_id] = []
    state.pending_stream_events.pop(meta.thread_id, None)
    state.stale_threads.discard(meta.thread_id)
    state.uploaded_files = []
    reset_chat_messages(state)
    state.waiting_for_approval = False
    state.approval_interrupted = False
    return state, _conversation_panel_update(state), list(state.messages), gr.update(value="")


async def _delete_thread_action(thread_id: Optional[str], state: UIState):
    if not thread_id or not state.user_id or len(state.thread_ids) <= 1:
        return state, _conversation_panel_update(state), list(state.messages), gr.update(value="")
    await delete_thread(state.user_id, thread_id)
    _delete_thread_data(state.user_id, thread_id)
    await _sync_user_threads(state, ensure_one=bool(state.user_id))
    return state, _conversation_panel_update(state), list(state.messages), gr.update(value="")


async def on_conversation_action(action_payload: str, state: UIState):
    if state is None:
        state = _initialize_state()
    payload = (action_payload or "").strip()
    if not payload:
        return state, _conversation_panel_update(state), list(state.messages), gr.update(value=""), gr.update(value="")
    try:
        action = json.loads(payload)
    except json.JSONDecodeError:
        return state, _conversation_panel_update(state), list(state.messages), gr.update(value=""), gr.update(value="")
    action_type = action.get("type")
    thread_id = action.get("thread_id")
    if action_type == "delete":
        result = await _delete_thread_action(thread_id, state)
    elif action_type == "activate":
        result = await _activate_thread(thread_id, state)
    else:
        result = (state, _conversation_panel_update(state), list(state.messages), gr.update(value=""))
    return (*result, gr.update(value=""))


async def on_files_uploaded(files, state: UIState):
    if state is None:
        state = _initialize_state()
    if not files or not state.user_id or not state.current_thread_id:
        return state, _conversation_panel_update(state)
    existing_hashes = {_hash_file(path) for path in _list_upload_files(state.current_thread_id, user_id=state.user_id)}
    for file_obj in files:
        destination, file_hash = _save_uploaded_file(file_obj, user_id=state.user_id, thread_id=state.current_thread_id)
        if file_hash in existing_hashes:
            destination.unlink(missing_ok=True)
            continue
        existing_hashes.add(file_hash)
    await _refresh_thread_files_for(state, state.current_thread_id)
    return state, _conversation_panel_update(state)


async def on_clear_files(state: UIState):
    if state is None:
        state = _initialize_state()
    if not state.user_id or not state.current_thread_id:
        return state, _conversation_panel_update(state)
    for path in _list_upload_files(state.current_thread_id, user_id=state.user_id):
        path.unlink(missing_ok=True)
    shutil.rmtree(thread_data_root(state.current_thread_id, user_id=state.user_id, create=False), ignore_errors=True)
    await _refresh_thread_files_for(state, state.current_thread_id)
    return state, _conversation_panel_update(state)


async def on_periodic_file_refresh(state: UIState):
    if state is None or not state.current_thread_id:
        return state, gr.update()
    updated = await _refresh_thread_files_for(state, state.current_thread_id)
    if not updated:
        return state, gr.update()
    return state, _conversation_panel_update(state)


def _append_file_paths(prompt: str, state: UIState) -> str:
    files = state.uploaded_files
    if not files:
        return prompt
    if len(files) == 1:
        return f"{prompt}\n\nUploaded file: {files[0].path}"
    return prompt + "\n\nUploaded files:\n" + "\n".join(f"- {file.path}" for file in files)


async def _run_user_message_internal(prompt: str, state: UIState):
    if not state.user_id or not state.current_thread_id:
        yield state, list(state.messages), gr.update(value=""), _conversation_panel_update(state)
        return

    prompt = (prompt or "").strip()
    resume = state.waiting_for_approval
    if not prompt:
        yield state, list(state.messages), gr.update(value=""), _conversation_panel_update(state)
        return

    final_prompt = prompt if resume else _append_file_paths(prompt, state)
    append_user_message(state, prompt)
    await _persist_timeline_snapshot(state.current_thread_id, state)

    user_messages = [message for message in state.messages if message.role == "user"]
    if len(user_messages) == 1:
        title = prompt[:60].strip() or DEFAULT_CONVERSATION_TITLE
        await update_thread_title(state.user_id, state.current_thread_id, title)
        await _sync_user_threads(state)

    state.waiting_for_approval = False
    state.approval_interrupted = False
    app_config = AppRunConfig(
        user_request=final_prompt,
        user_id=state.user_id,
        conversation_id=state.current_thread_id,
    )
    state.current_app_config = app_config

    yield state, list(state.messages), gr.update(value=""), _conversation_panel_update(state)

    thread_id = state.current_thread_id
    state.selected_thread_id = thread_id
    state.running_threads.add(thread_id)
    state.stop_signals[thread_id] = False
    try:
        stream_iter = stream_langgraph_events(
            app_config,
            build_stream_input(
                final_prompt,
                user_id=state.user_id,
                conversation_id=thread_id,
                resume=resume,
            ),
            thread_id,
            check_for_interrupts=True,
        )
        stream_iter_closed = False
        stream_task = asyncio.create_task(stream_iter.__anext__())
        poll_task = (
            asyncio.create_task(asyncio.sleep(FILE_LIST_REFRESH_INTERVAL_SECONDS))
            if thread_id
            else None
        )
        ui_attached = True
        stopped = False
        try:
            while stream_task:
                wait_tasks = [stream_task]
                if poll_task:
                    wait_tasks.append(poll_task)
                done, _ = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)

                selected_thread = state.selected_thread_id or state.current_thread_id
                is_active_thread = selected_thread == thread_id
                if is_active_thread and not ui_attached:
                    await _refresh_conversation(state, thread_id)
                    updated = _drain_pending_stream_events(state, thread_id)
                    if updated:
                        yield (
                            state,
                            list(state.messages),
                            gr.update(value=""),
                            _conversation_panel_update(state),
                        )
                    ui_attached = True
                elif not is_active_thread:
                    state.stale_threads.add(thread_id)
                    ui_attached = False

                if poll_task and poll_task in done:
                    poll_task = asyncio.create_task(asyncio.sleep(FILE_LIST_REFRESH_INTERVAL_SECONDS))
                    if ui_attached and await _refresh_thread_files_for(state, thread_id):
                        yield state, list(state.messages), gr.update(value=""), _conversation_panel_update(state)

                if stream_task in done:
                    try:
                        event_type, payload = stream_task.result()
                    except StopAsyncIteration:
                        stream_task = None
                        break
                    except Exception as exc:
                        if ui_attached:
                            updated = _record_stream_error(state, exc)
                            await _persist_timeline_snapshot(thread_id, state)
                        else:
                            timeline_state = UIState()
                            snapshot = await load_timeline(state.user_id, thread_id) if state.user_id else None
                            if isinstance(snapshot, dict):
                                rebuild_from_timeline_snapshot(timeline_state, snapshot)
                            updated = _record_stream_error(timeline_state, exc)
                            if updated and state.user_id:
                                await save_timeline(state.user_id, thread_id, export_timeline_snapshot(timeline_state))
                            _clear_pending_chunk_events(state, thread_id)
                        if ui_attached and updated:
                            yield (
                                state,
                                list(state.messages),
                                gr.update(value=""),
                                _conversation_panel_update(state),
                            )
                        if stream_task:
                            stream_task = None
                        if poll_task:
                            poll_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await poll_task
                            poll_task = None
                        with suppress(Exception):
                            await stream_iter.aclose()
                        stream_iter_closed = True
                        break

                    if event_type == "complete":
                        interrupted, completed_at = _parse_complete_payload(payload)
                        if not interrupted:
                            state.last_run_at[thread_id] = completed_at or datetime.now(timezone.utc)

                    if not ui_attached:
                        if event_type in {"chunk", "complete"}:
                            state.pending_stream_events.setdefault(thread_id, []).append((event_type, payload))
                        await _apply_event_to_persisted_timeline(state, thread_id, event_type, payload)
                        if event_type == "ai_message":
                            _clear_pending_chunk_events(state, thread_id)
                        stream_task = asyncio.create_task(stream_iter.__anext__())
                        continue

                    updated = _apply_stream_event(event_type, payload, state)
                    if updated:
                        await _persist_timeline_snapshot(thread_id, state)
                        await _refresh_thread_files_for(state, thread_id)
                        yield state, list(state.messages), gr.update(value=""), _conversation_panel_update(state)
                    stream_task = asyncio.create_task(stream_iter.__anext__())

                if state.stop_signals.get(thread_id):
                    stopped = True
                    state.waiting_for_approval = False
                    state.approval_interrupted = False
                    state.current_app_config = None
                    if stream_task:
                        stream_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await stream_task
                        stream_task = None
                    if poll_task:
                        poll_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await poll_task
                        poll_task = None
                    with suppress(Exception):
                        await stream_iter.aclose()
                    stream_iter_closed = True
                    break
        finally:
            if poll_task:
                poll_task.cancel()
                with suppress(asyncio.CancelledError):
                    await poll_task
            if stream_task:
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task
            if not stream_iter_closed:
                with suppress(Exception):
                    await stream_iter.aclose()
    finally:
        state.running_threads.discard(thread_id)
        state.stop_signals.pop(thread_id, None)

    selected_thread = state.selected_thread_id or state.current_thread_id
    is_active_thread = selected_thread == thread_id
    if not is_active_thread:
        state.stale_threads.add(thread_id)
    elif not stopped and await _refresh_thread_files_for(state, thread_id):
        yield state, list(state.messages), gr.update(value=""), _conversation_panel_update(state)


_thread_locks: Dict[str, asyncio.Lock] = {}
_thread_locks_guard = asyncio.Lock()


async def _get_thread_lock(thread_id: str) -> asyncio.Lock:
    async with _thread_locks_guard:
        lock = _thread_locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            _thread_locks[thread_id] = lock
        return lock


@asynccontextmanager
async def _thread_execution_lock(thread_id: Optional[str]):
    if not thread_id:
        yield
        return
    lock = await _get_thread_lock(thread_id)
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()


async def _run_user_message(prompt: str, state: UIState):
    async with _thread_execution_lock(state.current_thread_id):
        async for update in _run_user_message_internal(prompt, state):
            yield update


async def on_send_message(prompt: str, state: UIState):
    if state is None:
        state = _initialize_state()
    async for update in _run_user_message(prompt, state):
        yield update


async def on_stop_run(state: UIState):
    if state is None:
        state = _initialize_state()
    thread_id = state.current_thread_id
    if not thread_id or thread_id not in state.running_threads:
        return state, list(state.messages), gr.update(), _conversation_panel_update(state)
    state.stop_signals[thread_id] = True
    return state, list(state.messages), gr.update(), _conversation_panel_update(state)


@FILES_ROUTER.get("/download")
async def download_file(token: str):
    payload = _decode_download_token(token)
    path_value = payload.get("path")
    if not path_value:
        raise HTTPException(status_code=400, detail="Missing file path")
    resolved_path = _safe_resolve(path_value)
    if not _is_allowed_download_path(resolved_path):
        raise HTTPException(status_code=403, detail="Access denied")
    await _validate_download_access(payload, resolved_path)
    if not resolved_path.exists() or not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    filename = payload.get("name") or resolved_path.name
    mime, _ = mimetypes.guess_type(filename)
    return FileResponse(resolved_path, filename=filename, media_type=mime or "application/octet-stream")


_CONVERSATION_SCRIPT = """
<script>
(function() {
    function findBus() {
        const el = document.getElementById("conversation-action-bus");
        if (!el) return null;
        if (el.matches && el.matches("textarea, input")) return el;
        return el.querySelector ? el.querySelector("textarea, input") : null;
    }

    function sendAction(payload) {
        const bus = findBus();
        if (!bus) return;
        bus.value = JSON.stringify(Object.assign({ ts: Date.now() }, payload || {}));
        bus.dispatchEvent(new Event("input", { bubbles: true }));
        bus.dispatchEvent(new Event("change", { bubbles: true }));
    }

    async function triggerDownload(anchor) {
        const url = anchor.getAttribute("href");
        if (!url) return;
        anchor.dataset.downloading = "1";
        try {
            const response = await fetch(url, { credentials: "same-origin" });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const blob = await response.blob();
            const filename = anchor.getAttribute("data-file-name") || anchor.textContent.trim() || "download";
            const blobUrl = window.URL.createObjectURL(blob);
            const temp = document.createElement("a");
            temp.href = blobUrl;
            temp.download = filename;
            document.body.appendChild(temp);
            temp.click();
            window.setTimeout(() => {
                document.body.removeChild(temp);
                window.URL.revokeObjectURL(blobUrl);
            }, 0);
        } catch (error) {
            console.error("Download failed", error);
            window.open(url, "_blank", "noopener");
        } finally {
            delete anchor.dataset.downloading;
        }
    }

    function bindHandlers() {
        const root = document.getElementById("conversation-list-root");
        if (!root) return;

        root.querySelectorAll("summary").forEach((summary) => {
            if (summary.dataset.repBound === "1") return;
            summary.dataset.repBound = "1";
            summary.addEventListener("click", (event) => {
                if (event.target && event.target.closest("[data-delete-thread]")) return;
                const parent = summary.closest("details");
                if (!parent) return;
                const threadId = parent.getAttribute("data-thread-id");
                if (threadId) sendAction({ type: "activate", thread_id: threadId });
            });
        });

        root.querySelectorAll("[data-delete-thread]").forEach((button) => {
            if (button.dataset.repBound === "1") return;
            button.dataset.repBound = "1";
            button.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                const threadId = button.getAttribute("data-delete-thread");
                const confirmMessage = button.getAttribute("data-confirm-message");
                if (!threadId) return;
                if (confirmMessage && !window.confirm(confirmMessage)) return;
                sendAction({ type: "delete", thread_id: threadId });
            });
        });

        root.querySelectorAll("[data-download-link]").forEach((link) => {
            if (link.dataset.repDownloadBound === "1") return;
            link.dataset.repDownloadBound = "1";
            link.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                if (link.dataset.downloading === "1") return;
                triggerDownload(link);
            });
        });
    }

    function initPartnerSlider(slider) {
        if (!slider || slider.dataset.sliderInitialized === "1") return;
        const viewport = slider.querySelector(".partner-slider__viewport");
        const track = slider.querySelector(".partner-slider__track");
        const cards = Array.from(slider.querySelectorAll(".partner-logo-card"));
        const dots = slider.querySelector(".partner-slider__dots");
        if (!viewport || !track || !cards.length || !dots) return;
        const state = { index: 0, perSlide: 1, total: 1 };

        function applyTransform() {
            const viewportWidth = viewport.getBoundingClientRect().width || 1;
            track.style.transform = `translateX(-${state.index * viewportWidth}px)`;
        }

        function goTo(index) {
            state.index = Math.max(0, Math.min(index, state.total - 1));
            applyTransform();
            renderDots();
        }

        function renderDots() {
            dots.innerHTML = "";
            if (state.total <= 1) {
                dots.style.display = "none";
                return;
            }
            dots.style.display = "flex";
            for (let i = 0; i < state.total; i += 1) {
                const dot = document.createElement("button");
                dot.type = "button";
                dot.className = "partner-slider__dot" + (i === state.index ? " is-active" : "");
                dot.addEventListener("click", () => goTo(i));
                dots.appendChild(dot);
            }
        }

        function recalc() {
            const viewportWidth = viewport.getBoundingClientRect().width || 1;
            const sampleWidth = cards[0].getBoundingClientRect().width || 1;
            const styles = window.getComputedStyle(track);
            const gap = parseFloat(styles.columnGap || styles.gap || "16") || 16;
            const perSlide = Math.max(1, Math.floor((viewportWidth + gap) / (sampleWidth + gap)));
            state.perSlide = perSlide;
            state.total = Math.max(1, Math.ceil(cards.length / perSlide));
            state.index = Math.min(state.index, state.total - 1);
            renderDots();
            applyTransform();
        }

        const requestRecalc = () => window.requestAnimationFrame(recalc);
        if (window.ResizeObserver) {
            const ro = new ResizeObserver(requestRecalc);
            ro.observe(viewport);
        } else {
            window.addEventListener("resize", requestRecalc);
        }
        requestRecalc();
        slider.dataset.sliderInitialized = "1";
    }

    function initPartnerSliders() {
        document.querySelectorAll("[data-partner-slider]").forEach((slider) => initPartnerSlider(slider));
    }

    function ensureReady() {
        bindHandlers();
        initPartnerSliders();
    }

    ensureReady();
    const observer = new MutationObserver(() => {
        window.requestAnimationFrame(ensureReady);
    });
    observer.observe(document.body, { childList: true, subtree: true });
})();
</script>
"""


def build_demo() -> gr.Blocks:
    extra_css = """
    :root {
        --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        --font-editorial: "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
        --page-bg: #f8f8f8;
        --surface-bg: #ffffff;
        --surface-muted: #fbfbf8;
        --surface-tint: #f5f7f8;
        --field-bg: #ffffff;
        --text-main: #1f1f1f;
        --text-soft: #5f6368;
        --border-subtle: #d5d5d5;
        --border-strong: #bcbcbc;
        --link-color: #025e8d;
        --link-hover-color: #01486d;
        --focus-color: #fece3e;
        --header-link-color: #1f1f1f;
        --header-link-divider-color: #8d8d8d;
        --header-link-hover-color: #025e8d;
        --partner-card-width: 220px;
        --partner-card-gap: 1.15rem;
    }
    body.dark {
        --header-link-color: #f8fafc;
        --header-link-divider-color: rgba(248, 250, 252, 0.65);
        --header-link-hover-color: #8ec3e4;
    }
    body,
    .gradio-container {
        font-family: var(--font-ui);
        background: var(--page-bg);
        color: var(--text-main);
    }
    .gradio-container *,
    .gradio-container *::before,
    .gradio-container *::after {
        box-sizing: border-box;
    }
    .gradio-container {
        max-width: none;
        width: 100vw;
        margin: 0 auto !important;
        padding: 1.5rem 1.25rem 2rem;
    }
    .gradio-container a {
        color: var(--link-color);
        text-decoration-thickness: 0.06em;
        text-underline-offset: 0.14em;
    }
    .gradio-container a:hover,
    .gradio-container a:focus {
        color: var(--link-hover-color);
    }
    .gradio-container button,
    .gradio-container input,
    .gradio-container textarea,
    .gradio-container label,
    .gradio-container .tabs,
    .gradio-container .tabitem {
        font-family: var(--font-ui) !important;
    }
    .gradio-container button:focus-visible,
    .gradio-container input:focus-visible,
    .gradio-container textarea:focus-visible,
    .gradio-container [role="tab"]:focus-visible,
    .conversation-card__delete:focus-visible,
    .partner-logo-card:focus-visible {
        outline: 3px solid var(--focus-color);
        outline-offset: 2px;
    }
    #app-header {
        align-items: center;
        gap: 1rem;
        margin-bottom: 0.8rem;
        padding: 0 0 0.9rem;
        border-bottom: 1px solid var(--border-strong);
    }
    #app-logo {
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0 !important;
    }
    #app-logo .app-logo-img {
        width: 84px;
        height: 84px;
        object-fit: contain;
        display: block;
    }
    #app-title {
        margin: 0 !important;
        padding: 0 !important;
        display: flex;
        align-items: center;
    }
    #app-title .app-title-text {
        font-family: var(--font-editorial);
        font-size: clamp(2.75rem, 4vw, 3.5rem);
        font-weight: 700;
        letter-spacing: -0.025em;
        line-height: 0.95;
        margin: 0;
        color: var(--text-main);
    }
    #header-links-column {
        margin-left: auto;
        padding: 0 !important;
        display: flex;
        justify-content: flex-end;
        align-items: center;
        overflow: visible !important;
    }
    #header-links {
        display: flex;
        gap: 0.75rem;
        align-items: center;
        font-weight: 600;
        font-size: 0.92rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--header-link-color);
        overflow: visible !important;
        white-space: nowrap;
    }
    #header-links .header-link {
        color: inherit;
        text-decoration: none;
        transition: color 0.2s ease;
        white-space: nowrap;
    }
    #header-links .header-link-divider {
        color: var(--header-link-divider-color);
        font-weight: 400;
        padding: 0 1.25rem;
        user-select: none;
    }
    #header-links .header-link:hover,
    #header-links .header-link:focus {
        color: var(--header-link-hover-color);
        text-decoration: underline;
    }
    #partner-logos-panel {
        width: 100%;
        margin: 0 auto 0.85rem;
        padding: 0.4rem 0 0.1rem;
        border-top: 1px solid var(--border-subtle);
        border-bottom: 1px solid var(--border-subtle);
        background: linear-gradient(180deg, #fafaf9 0%, #f8f8f8 100%);
    }
    #partner-logos-panel .partner-slider {
        width: 100%;
        margin: 0;
    }
    .partner-slider__viewport {
        overflow: hidden;
        width: 100%;
    }
    .partner-slider__track {
        display: flex;
        gap: var(--partner-card-gap);
        padding: 0.25rem;
        will-change: transform;
        transition: transform 0.4s ease;
    }
    .partner-logo-card {
        background: var(--surface-bg);
        border: 1px solid var(--border-subtle);
        border-radius: 0;
        padding: 1rem 1.7rem;
        min-height: 108px;
        min-width: var(--partner-card-width);
        width: var(--partner-card-width);
        max-width: var(--partner-card-width);
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: none;
        transition: background 0.15s ease, border-color 0.15s ease;
        flex: 0 0 var(--partner-card-width);
    }
    .partner-logo-card:hover,
    .partner-logo-card:focus-visible {
        background: #fcfcf9;
        border-color: var(--link-color);
    }
    .partner-logo-card img {
        max-height: 70px;
        max-width: calc(var(--partner-card-width) - 20px);
        width: auto;
        height: auto;
        object-fit: contain;
        filter: saturate(1.05);
    }
    .partner-logo-card--xl {
        min-width: calc(var(--partner-card-width) + 60px);
        width: calc(var(--partner-card-width) + 60px);
        max-width: calc(var(--partner-card-width) + 60px);
    }
    .partner-logo-card--xl img {
        max-height: 90px;
        max-width: calc(var(--partner-card-width) + 20px);
    }
    .partner-slider__dots { display: flex; justify-content: center; gap: 0.45rem; margin-top: 0.5rem; }
    .partner-slider__dot { width: 9px; height: 9px; border-radius: 999px; background: #d5d5d5; border: 0; cursor: pointer; transition: all 0.2s ease; }
    .partner-slider__dot.is-active { background: var(--link-color); border: 0; }
    #intro-text {
        margin: 0 0 0.18rem 0 !important;
        padding: 0;
        width: 100%;
    }
    #intro-text,
    #intro-text *,
    #chatbot-panel,
    #chatbot-panel .prose,
    #chatbot-panel .prose p,
    #chatbot-panel .prose li,
    #chatbot-panel .message,
    #chatbot-panel [data-testid*="assistant"],
    #chatbot-panel [data-testid*="assistant"] * {
        font-family: var(--font-editorial) !important;
    }
    #intro-text img {
        width: 100%;
        max-width: 100%;
        max-height: 330px;
        height: auto;
        display: block;
        margin: 0 auto;
        border-radius: 0;
        border: 1px solid var(--border-subtle);
        box-shadow: none;
        object-fit: contain;
        background: #fff;
    }
    #layout-row {
        width: 100%;
        gap: 1.5rem;
        align-items: flex-start;
    }
    #layout-row > div {
        min-width: 0;
    }
    #conversation-column {
        display: flex;
        flex-direction: column;
        gap: 0.55rem;
        padding: 1rem 1rem 1.1rem;
        background: var(--page-bg);
        border: 1px solid transparent;
        flex: 1 1 0 !important;
        width: auto !important;
        min-width: 0 !important;
    }
    #sidebar-column {
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
        position: sticky;
        top: 1rem;
        align-self: flex-start;
        min-width: 312px;
        width: 312px !important;
        flex: 0 0 312px !important;
        max-width: 312px;
    }
    #sidebar-column > div {
        background: var(--page-bg);
        border: 1px solid transparent;
        border-radius: 0;
        box-shadow: none;
        padding: 0.2rem;
    }
    #conversation-column > div {
        background: transparent;
        border: 0;
        border-radius: 0;
        box-shadow: none;
    }
    #conversation-list {
        background: transparent !important;
        border: 0 !important;
        box-shadow: none !important;
        padding: 0 !important;
    }
    #conversation-list > div {
        background: transparent !important;
        border: 0 !important;
        box-shadow: none !important;
    }
    #conversation-action-bus { display: none !important; }
    #input-actions-row {
        margin-top: 0.15rem;
        gap: 0.65rem;
        padding-top: 0.35rem;
        border-top: 1px solid var(--border-subtle);
        align-items: stretch;
    }
    #send-button, #stop-button { width: 100%; }
    #stop-button { min-width: 120px; }
    #auth-status {
        padding: 0.65rem 0.75rem !important;
        background: var(--surface-tint);
    }
    #auth-status p {
        margin: 0 !important;
        font-family: var(--font-ui) !important;
        font-size: 0.88rem !important;
        line-height: 1.45 !important;
    }
    #auth-tabs {
        padding: 0.2rem !important;
    }
    #auth-tabs > div,
    #auth-tabs > div > div,
    #auth-tabs .tabitem,
    #auth-tabs .tabitem > div {
        background: transparent !important;
        box-shadow: none !important;
    }
    #auth-tabs .tabitem {
        border: 0 !important;
        padding: 0.9rem 0.35rem 0.2rem !important;
    }
    #auth-tabs .tabitem > div {
        border: 0 !important;
        padding: 0 !important;
    }
    #sidebar-column .tabs,
    #sidebar-column .tab-nav,
    #sidebar-column .tabitem {
        border-radius: 0 !important;
    }
    #sidebar-column .tab-nav {
        border-bottom: 1px solid var(--border-subtle) !important;
        padding: 0 0.25rem !important;
    }
    #sidebar-column [role="tab"] {
        border-radius: 0 !important;
        border-bottom: 2px solid transparent !important;
        padding: 0.7rem 0.2rem 0.65rem !important;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        font-size: 0.74rem !important;
        font-weight: 700 !important;
    }
    #sidebar-column [role="tab"][aria-selected="true"] {
        color: var(--link-color) !important;
        border-bottom-color: var(--link-color) !important;
    }
    #sidebar-column input,
    #sidebar-column textarea,
    #conversation-column textarea {
        border-radius: 0 !important;
        border: 1px solid var(--border-strong) !important;
        background: var(--field-bg) !important;
        color: var(--text-main) !important;
    }
    #sidebar-column input:focus,
    #sidebar-column textarea:focus,
    #conversation-column textarea:focus {
        background: var(--field-bg) !important;
        border-color: var(--link-color) !important;
        box-shadow: none !important;
    }
    #sidebar-column input:-webkit-autofill,
    #sidebar-column input:-webkit-autofill:hover,
    #sidebar-column input:-webkit-autofill:focus,
    #conversation-column textarea:-webkit-autofill,
    #conversation-column textarea:-webkit-autofill:hover,
    #conversation-column textarea:-webkit-autofill:focus {
        -webkit-text-fill-color: var(--text-main) !important;
        -webkit-box-shadow: 0 0 0 1000px var(--field-bg) inset !important;
        transition: background-color 9999s ease-out 0s;
    }
    #sidebar-column label,
    #conversation-column label {
        font-size: 0.84rem !important;
        font-weight: 700 !important;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--text-soft) !important;
    }
    #sidebar-column button,
    #conversation-column button {
        border-radius: 0 !important;
        box-shadow: none !important;
        font-weight: 700 !important;
        letter-spacing: 0.02em;
    }
    #logout-button,
    #new-task-button,
    #clear-files-button {
        min-height: 42px;
    }
    #new-task-button button:disabled {
        background: #efefef !important;
        color: #8d8d8d !important;
        border-color: var(--border-subtle) !important;
        opacity: 1 !important;
    }
    #stop-button button,
    #sidebar-column button.secondary {
        border: 1px solid var(--border-strong) !important;
    }
    #file-upload-panel {
        background: transparent !important;
        border: 0 !important;
        padding: 0 !important;
        box-shadow: none !important;
        width: 100% !important;
    }
    #file-upload-panel > div,
    #file-upload-panel > div > div {
        background: transparent !important;
        border: 0 !important;
        box-shadow: none !important;
        width: 100% !important;
    }
    #file-upload-panel .wrap {
        border: 1px dashed var(--border-strong) !important;
        background: linear-gradient(180deg, #ffffff 0%, #fbfbf8 100%) !important;
        min-height: 150px;
        padding: 0.85rem !important;
        width: 100% !important;
        max-width: 100% !important;
    }
    #file-upload-panel .or,
    #file-upload-panel .hint {
        color: var(--text-soft) !important;
    }
    #file-upload-panel .label-wrap,
    #file-upload-panel .label-wrap label {
        background: transparent !important;
        box-shadow: none !important;
    }
    #file-upload-panel .label-wrap {
        width: 100% !important;
        margin-bottom: 0 !important;
    }
    #file-upload-panel .label-wrap label {
        display: block !important;
        width: 100% !important;
    }
    #file-upload-panel .center,
    #file-upload-panel .upload-container,
    #file-upload-panel [data-testid="file-upload-dropzone"] {
        width: 100% !important;
        max-width: 100% !important;
    }
    #intro-text {
        padding-bottom: 0.3rem;
        border-bottom: 1px solid var(--border-subtle);
    }
    #chatbot-panel {
        font-size: 1.03rem;
        line-height: 1.68;
        border-radius: 0 !important;
        background: var(--surface-bg) !important;
        border: 1px solid var(--border-subtle) !important;
    }
    #chatbot-panel .prose,
    #chatbot-panel .prose p,
    #chatbot-panel .bot-message *,
    #chatbot-panel .message.bot *,
    #chatbot-panel [data-testid*="assistant"],
    #chatbot-panel [data-testid*="assistant"] * {
        font-size: inherit !important;
        line-height: inherit !important;
        color: var(--text-main) !important;
    }
    #chatbot-panel .user-message *,
    #chatbot-panel .message.user *,
    #chatbot-panel [data-testid*="user"],
    #chatbot-panel [data-testid*="user"] * {
        font-size: 0.98rem !important;
        line-height: 1.6 !important;
        font-family: var(--font-ui) !important;
    }
    #chatbot-panel [data-testid="chatbot-avatar"] {
        border-radius: 0 !important;
    }
    #chatbot-panel .bubble-wrap,
    #chatbot-panel .message-wrap {
        padding-left: 0.15rem !important;
        padding-right: 0.15rem !important;
    }
    #chatbot-panel .message,
    #chatbot-panel .message-row {
        border-radius: 0 !important;
    }
    #chatbot-panel [data-testid*="assistant"] {
        background: #fff !important;
    }
    #chatbot-panel [data-testid*="user"] {
        background: #f4f7f9 !important;
        border: 1px solid #d8e2e7 !important;
    }
    #chatbot-panel code,
    #chatbot-panel pre,
    .tool-code-block pre,
    details.tool-block pre {
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace !important;
    }
    .agent-message-inline {
        margin: 0.9rem 0;
        white-space: pre-wrap;
        word-break: break-word;
        color: var(--text-main);
    }
    details.tool-block {
        border: 1px solid var(--border-subtle);
        border-radius: 0;
        padding: 0.75rem 0.95rem;
        background: var(--surface-muted);
        margin: 0.9rem 0;
    }
    details.tool-block summary {
        font-family: var(--font-ui);
        font-weight: 700;
        color: var(--text-main);
        cursor: pointer;
        letter-spacing: 0.02em;
    }
    details.tool-block pre {
        margin: 0.75rem 0 0 0;
        font-size: 0.92rem;
        background: #f4f7f9;
        padding: 0.85rem 1rem;
        border-radius: 0;
        overflow-x: auto;
        white-space: pre-wrap;
        border: 1px solid var(--border-subtle);
    }
    .tool-code-block {
        background: #f4f7f9;
        border: 1px solid var(--border-subtle);
        border-radius: 0;
        padding: 0.95rem 1rem;
        margin-top: 0.75rem;
        overflow-x: auto;
    }
    .tool-code-label {
        font-family: var(--font-ui);
        font-size: 0.72rem;
        letter-spacing: 0.1em;
        font-weight: 700;
        color: var(--text-soft);
        margin-bottom: 0.45rem;
    }
    .tool-code-block pre {
        margin: 0;
        font-size: 0.92rem;
        line-height: 1.55;
        color: var(--text-main);
        background: transparent;
        white-space: pre;
    }
    #user-input {
        border: 0 !important;
        background: transparent !important;
        padding: 0 !important;
        box-shadow: none !important;
    }
    #user-input > div,
    #user-input > div > div {
        border: 0 !important;
        background: transparent !important;
        box-shadow: none !important;
        padding: 0 !important;
    }
    #user-input textarea {
        min-height: 112px !important;
        line-height: 1.6 !important;
        padding: 0.8rem 0.9rem !important;
        border: 1px solid var(--border-subtle) !important;
    }
    #send-button button {
        min-height: 46px;
    }
    #stop-button button {
        min-height: 46px;
        background: #fff !important;
    }
    #conversation-list {
        margin-top: 0.5rem;
        font-family: var(--font-ui);
        width: 100%;
        display: block;
    }
    #conversation-list,
    #conversation-list > div,
    #conversation-list-root {
        width: 100%;
        box-sizing: border-box;
    }
    #conversation-list-root {
        border: 1px solid var(--border-subtle);
        border-radius: 0;
        background: var(--surface-bg);
        overflow: hidden;
        box-shadow: none;
    }
    .conversation-list__header {
        font-weight: 700;
        padding: 0.8rem 0.95rem;
        border-bottom: 1px solid var(--border-subtle);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.74rem;
        color: var(--text-soft);
        background: var(--surface-muted);
    }
    details.conversation-card { border-bottom: 1px solid #ececec; }
    details.conversation-card:last-child { border-bottom: none; }
    details.conversation-card summary {
        list-style: none;
        padding: 0.82rem 0.95rem;
        cursor: pointer;
        background: transparent;
        transition: background 0.2s ease, color 0.2s ease;
    }
    details.conversation-card summary::-webkit-details-marker { display: none; }
    details.conversation-card.is-active summary {
        background: #ebf6ff;
        color: var(--text-main);
    }
    .conversation-card__title-row { display: flex; align-items: flex-start; gap: 0.55rem; }
    .conversation-card__title { font-size: 0.9rem; font-weight: 600; color: inherit; flex: 1; line-height: 1.28; }
    .conversation-card__chevron { width: 12px; height: 12px; border-right: 2px solid currentColor; border-bottom: 2px solid currentColor; transform: rotate(45deg); transition: transform 0.2s ease; }
    .conversation-card__chevron { margin-top: 0.32rem; flex: 0 0 12px; }
    details.conversation-card[open] .conversation-card__chevron { transform: rotate(-135deg); }
    .conversation-card__delete {
        border: 1px solid var(--border-strong);
        border-radius: 0;
        padding: 0;
        font-size: 0.8rem;
        background: var(--surface-bg);
        cursor: pointer;
        color: var(--text-soft);
        transition: background 0.2s ease, color 0.2s ease, border-color 0.2s ease;
        margin-left: auto;
        width: 2rem;
        min-width: 2rem;
        height: 2rem;
        display: inline-flex;
        align-items: center;
        justify-content: center;
    }
    .conversation-card__delete:hover {
        background: var(--surface-muted);
        color: var(--text-main);
        border-color: var(--link-color);
    }
    .conversation-card__body {
        background: var(--surface-muted);
        padding: 0.55rem 0.95rem 0.9rem;
        border-top: 1px solid #ececec;
    }
    .conversation-card__files-container { max-height: 180px; overflow-y: auto; padding-right: 0.25rem; }
    .conversation-card__files { list-style: none; margin: 0; padding: 0; }
    .conversation-card__file-item { font-size: 0.82rem; color: var(--text-main); }
    .conversation-card__file-name { font-weight: 500; }
    .conversation-card__file-link { font-weight: 600; color: var(--link-color); text-decoration: none; }
    .conversation-card__file-link:hover, .conversation-card__file-link:focus { text-decoration: underline; }
    .conversation-card__file-more, .conversation-card__empty { font-size: 0.82rem; color: var(--text-soft); margin: 0; }
    footer {
        border-top: 1px solid var(--border-subtle);
        margin-top: 1rem !important;
        padding-top: 0.85rem !important;
        color: var(--text-soft) !important;
    }
    @media (max-width: 900px) {
        .gradio-container {
            width: 100vw;
            padding-top: 1rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }
        #app-header {
            gap: 0.75rem;
        }
        #header-links {
            font-size: 0.8rem;
            letter-spacing: 0.06em;
        }
        #layout-row {
            gap: 1rem;
        }
        #sidebar-column {
            position: static;
        }
        #conversation-column {
            padding: 0.8rem;
        }
        #chatbot-panel {
            font-size: 1rem;
            line-height: 1.62;
        }
    }
    """

    with gr.Blocks(title=APP_TITLE, theme=CHEMSAFE_THEME, css=extra_css, head=_CONVERSATION_SCRIPT) as demo:
        state = gr.State()

        with gr.Row(elem_id="app-header"):
            logo_markup = _logo_html()
            if logo_markup:
                with gr.Column(scale=0, min_width=96):
                    gr.HTML(logo_markup, elem_id="app-logo")
            with gr.Column(scale=1):
                gr.HTML(f"<div class='app-title-text'>{APP_TITLE}</div>", elem_id="app-title")
            with gr.Column(scale=0, min_width=260, elem_id="header-links-column"):
                gr.HTML(HEADER_LINKS_HTML, elem_id="header-links")

        partner_panel = _partner_logos_html()
        if partner_panel:
            gr.HTML(partner_panel, elem_id="partner-logos-panel")

        with gr.Row(elem_id="layout-row"):
            with gr.Column(scale=1, min_width=280, elem_id="sidebar-column"):
                auth_status_md = gr.Markdown(value="**Login to use the ChemSafeAgent**", elem_id="auth-status")
                with gr.Tabs(elem_id="auth-tabs"):
                    with gr.Tab("Login"):
                        login_email = gr.Textbox(label="Email", placeholder="you@example.com")
                        login_password = gr.Textbox(label="Password", type="password")
                        login_btn = gr.Button("Log in", variant="primary")
                    with gr.Tab("Register"):
                        register_email = gr.Textbox(label="Email", placeholder="you@example.com")
                        register_password = gr.Textbox(label="Password", type="password")
                        register_confirm = gr.Textbox(label="Confirm Password", type="password")
                        register_btn = gr.Button("Create account")
                logout_btn = gr.Button("Log out", visible=False, elem_id="logout-button")

                conversation_list = gr.HTML(value="", elem_id="conversation-list", min_height=10, container=False)
                conversation_action_bus = gr.Textbox(value="", show_label=False, elem_id="conversation-action-bus")
                file_refresh_timer = gr.Timer(value=FILE_LIST_REFRESH_INTERVAL_SECONDS, active=True, render=False)
                new_task_btn = gr.Button("New Task", elem_id="new-task-button")
                file_upload = gr.File(label="Upload files", file_count="multiple", file_types=["file"], elem_id="file-upload-panel")
                clear_files_btn = gr.Button("Clear Files", elem_id="clear-files-button")

            with gr.Column(scale=4, elem_id="conversation-column"):
                gr.Markdown(INTRO_MARKDOWN, elem_id="intro-text")
                chatbot = gr.Chatbot(label="Conversation", height=560, type='messages', elem_id="chatbot-panel")
                user_input = gr.Textbox(label="Your message", lines=3, elem_id="user-input")
                with gr.Row(elem_id="input-actions-row"):
                    with gr.Column(scale=9):
                        send_btn = gr.Button("Send", variant="primary", elem_id="send-button")
                    with gr.Column(scale=1, min_width=120):
                        stop_btn = gr.Button("Stop", variant="secondary", elem_id="stop-button")

        demo.load(
            on_app_load,
            inputs=None,
            outputs=[
                state,
                auth_status_md,
                logout_btn,
                conversation_list,
                chatbot,
                user_input,
                conversation_action_bus,
                login_btn,
                new_task_btn,
            ],
        )

        conversation_action_bus.change(
            on_conversation_action,
            inputs=[conversation_action_bus, state],
            outputs=[state, conversation_list, chatbot, user_input, conversation_action_bus],
        )

        file_refresh_timer.tick(
            on_periodic_file_refresh,
            inputs=[state],
            outputs=[state, conversation_list],
            trigger_mode="always_last",
        )

        login_btn.click(
            on_login,
            inputs=[login_email, login_password, state],
            outputs=[state, auth_status_md, logout_btn, conversation_list, chatbot, login_btn, new_task_btn],
        )

        register_btn.click(
            on_register,
            inputs=[register_email, register_password, register_confirm, state],
            outputs=[state, auth_status_md, logout_btn, login_btn],
        )

        logout_btn.click(
            on_logout,
            inputs=state,
            outputs=[state, auth_status_md, logout_btn, conversation_list, chatbot, login_btn, new_task_btn],
        )

        new_task_btn.click(
            on_new_task,
            inputs=state,
            outputs=[state, conversation_list, chatbot, user_input],
        )

        file_upload.upload(
            on_files_uploaded,
            inputs=[file_upload, state],
            outputs=[state, conversation_list],
        )

        clear_files_btn.click(
            on_clear_files,
            inputs=state,
            outputs=[state, conversation_list],
        )

        send_btn.click(
            on_send_message,
            inputs=[user_input, state],
            outputs=[state, chatbot, user_input, conversation_list],
        )
        user_input.submit(
            on_send_message,
            inputs=[user_input, state],
            outputs=[state, chatbot, user_input, conversation_list],
        )

        stop_btn.click(
            on_stop_run,
            inputs=state,
            outputs=[state, chatbot, user_input, conversation_list],
        )

    return demo.queue(default_concurrency_limit=4, max_size=32)


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    await get_async_pool()
    await get_postgres_checkpointer()
    await AUTH_SERVICE.repo.ensure_schema()
    try:
        yield
    finally:
        await close_postgres_checkpointer()
        await close_async_pool()


def create_fastapi_app() -> FastAPI:
    demo = build_demo()
    fastapi_app = FastAPI(title=APP_TITLE, lifespan=_app_lifespan)
    fastapi_app.include_router(FILES_ROUTER)
    return gr.mount_gradio_app(fastapi_app, demo, path="/")


def launch() -> None:
    uvicorn.run(
        create_fastapi_app(),
        host=GRADIO_SERVER_NAME,
        port=GRADIO_SERVER_PORT,
    )
