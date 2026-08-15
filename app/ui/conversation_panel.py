from __future__ import annotations
import gradio as gr
from html import escape
from typing import List

import base64
import io
from functools import lru_cache
from pathlib import Path
from typing import Optional
from PIL import Image

from app.config import logger
from app.downloads import DOWNLOAD_ROUTE, build_download_payload, encode_download_token
from app.state import UIState

MAX_VISIBLE_FILES = 100
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_THUMBNAILS = 8
THUMBNAIL_MAX_PX = 220

@lru_cache(maxsize=64)
def _encode_thumbnail(path_value: str, mtime_ns: int, size: int) -> Optional[str]:
    """Encode one image to a data URI. Keyed on identity *and* content."""
    del mtime_ns, size  # part of the cache key only
    if Image is None:
        return None
    try:
        with Image.open(path_value) as image:
            image.load()
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            image.thumbnail((THUMBNAIL_MAX_PX, THUMBNAIL_MAX_PX))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
    except Exception as exc:  # noqa: BLE001 - a bad image must not break the sidebar
        logger.debug("Could not thumbnail %s: %s", path_value, exc)
        return None
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _thumbnail_data_uri(path_value: str) -> Optional[str]:
    """A small inline preview of an image artifact, or None."""
    path = Path(path_value)
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return _encode_thumbnail(str(path), stat.st_mtime_ns, stat.st_size)


def _render_thread_files(state: UIState, thread_id: str) -> str:
    files = state.thread_files.get(thread_id, [])
    if not files:
        return "<p class='conversation-card__empty'>No output files yet.</p>"
    items: List[str] = []
    thumbnails_used = 0
    for record in files[:MAX_VISIBLE_FILES]:
        payload = build_download_payload(
            record,
            thread_id,
            user_id=state.user_id,
            session_token=state.session_token,
        )
        if payload:
            token = encode_download_token(payload)
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

        preview = ""
        if thumbnails_used < MAX_THUMBNAILS:
            data_uri = _thumbnail_data_uri(record.path)
            if data_uri:
                thumbnails_used += 1
                preview = (
                    "<div class='conversation-card__thumb'>"
                    f"<img src='{escape(data_uri, quote=True)}' "
                    f"alt='{escape(record.name, quote=True)}' loading='lazy' /></div>"
                )

        items.append(
            "<li class='conversation-card__file-item' title='{title}'>{name}{preview}</li>".format(
                title=escape(record.path),
                name=name_markup,
                preview=preview,
            )
        )
    more_indicator = ""
    if len(files) > MAX_VISIBLE_FILES:
        more_indicator = f"<li class='conversation-card__file-more'>+{len(files) - MAX_VISIBLE_FILES} more…</li>"
    return (
        "<div class='conversation-card__files-container'>"
        "<ul class='conversation-card__files'>{}</ul>{}</div>"
    ).format("".join(items), more_indicator)


def _thread_badge(state: UIState, thread_id: str, *, is_active: bool) -> str:
    """Status dot for a thread the user is not currently looking at."""
    if thread_id in state.running_threads:
        return (
            "<span class='conversation-card__badge conversation-card__badge--running' "
            "title='Still running'>●</span>"
        )
    if not is_active and thread_id in state.stale_threads:
        return (
            "<span class='conversation-card__badge conversation-card__badge--updated' "
            "title='New activity since you last viewed this'>●</span>"
        )
    return ""


def conversation_panel_markup(state: UIState) -> str:
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
        # Files are rendered for the active card only. Clicking any other card activates that thread and re-renders, so a collapsed card's list is
        # never actually read — and building them all made every sidebar
        # refresh walk the filesystem once per conversation.
        body = _render_thread_files(state, thread_id) if is_active else ""
        cards.append(
            "<details class='conversation-card {active}' data-thread-id='{tid}' {open_attr}>"
            "<summary>"
            "<div class='conversation-card__title-row'>"
            "<span class='conversation-card__chevron' aria-hidden='true'></span>"
            "<span class='conversation-card__title'>{title}</span>"
            "{badge}"
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
                badge=_thread_badge(state, thread_id, is_active=is_active),
                files=body,
            )
        )
    cards.append("</div>")
    return "\n".join(cards)


def conversation_panel_update(state: UIState):
    """Send the sidebar only when it actually differs from what was last sent.

    `gr.HTML` swaps its whole DOM subtree whenever its value changes, taking the
    scroll position with it. During a run this fired on every streamed event, so
    the file list could not be scrolled while the agent worked.
    """
    markup = conversation_panel_markup(state)
    if markup == state.last_panel_markup:
        return gr.skip()
    state.last_panel_markup = markup
    return gr.update(value=markup)


def invalidate_panel_cache(state: UIState) -> None:
    """Force the next render to send the sidebar, whatever it looks like."""
    state.last_panel_markup = None


def thread_to_dict(meta) -> dict:
    return {
        "thread_id": meta.thread_id,
        "title": meta.title,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "user_id": meta.user_id,
    }


def append_file_paths(prompt: str, state: UIState) -> str:
    """Tell the agent where the user's uploads landed."""
    files = state.uploaded_files
    if not files:
        return prompt
    if len(files) == 1:
        return f"{prompt}\n\nUploaded file: {files[0].path}"
    return prompt + "\n\nUploaded files:\n" + "\n".join(f"- {file.path}" for file in files)
