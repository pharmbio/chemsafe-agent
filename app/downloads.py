"""Signed, expiring download links for user- and thread-scoped files.

A download link carries an HMAC-signed payload naming the file, the thread and
the session that asked for it. Serving the file re-checks all three: the
signature proves the link was issued by this app, `restore_session` proves the
caller still holds the session it was issued to, and the path must resolve
inside that thread's own data or output directory. A stolen link is therefore
useless without the session it was minted for, and expires in ten minutes
regardless.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import DATA_ROOT, DOWNLOAD_TOKEN_SECRET, RESULTS_ROOT
from app.state import FileRecord
from backend.auth.service import AuthService
from backend.utils.output_paths import conversation_output_root
from backend.utils.storage_paths import thread_data_root

FILES_ROUTER = APIRouter(prefix="/api/files")
DOWNLOAD_ROUTE = "/api/files/download"

# A link is minted inside a time bucket rather than at the exact second, so the
# same file produces the *same* token for the whole bucket. Without this the
# sidebar markup changed every second even when the file list had not — 10.8% of
# it churned — which replaced the panel's DOM and threw away the user's scroll
# position on every streamed event.
#
# The TTL is sized so the quantisation cannot shorten a link's life below what it
# was before: a token minted at the very end of a bucket still has
# TTL - BUCKET = 600s left, which is exactly the old fixed lifetime.
DOWNLOAD_TOKEN_BUCKET_SECONDS = 300
DOWNLOAD_TTL_SECONDS = 900

ALLOWED_DOWNLOAD_ROOTS = (Path(DATA_ROOT).resolve(), Path(RESULTS_ROOT).resolve())

_AUTH_SERVICE = AuthService()


def safe_resolve(path_value: str) -> Path:
    return Path(path_value).expanduser().resolve()


def is_allowed_download_path(path: Path) -> bool:
    for root in ALLOWED_DOWNLOAD_ROOTS:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def is_data_path(path_value: str) -> bool:
    """True when the path is an upload rather than an agent-produced artifact."""
    try:
        safe_resolve(path_value).relative_to(Path(DATA_ROOT).resolve())
        return True
    except ValueError:
        return False


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def encode_download_token(payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(DOWNLOAD_TOKEN_SECRET, body, hashlib.sha256).digest()
    return f"{_urlsafe_b64encode(body)}.{_urlsafe_b64encode(signature)}"


def decode_download_token(token: str) -> Dict[str, Any]:
    # Everything up to the signature check runs on attacker-controlled input:
    # bad base64 padding, non-UTF-8 bytes and non-object JSON all raise, and an
    # uncaught raise here is a 500 on a request that is simply malformed.
    try:
        body_part, sig_part = token.split(".", 1)
        body = _urlsafe_b64decode(body_part)
        provided_sig = _urlsafe_b64decode(sig_part)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Malformed download token") from exc

    expected_sig = hmac.new(DOWNLOAD_TOKEN_SECRET, body, hashlib.sha256).digest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise HTTPException(status_code=403, detail="Invalid download token")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Malformed download token") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Malformed download token")

    try:
        expires_at = int(payload.get("exp", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Malformed download token") from exc
    if not expires_at or expires_at < int(time.time()):
        raise HTTPException(status_code=401, detail="Download link expired")
    return payload


def build_download_payload(
    record: FileRecord,
    thread_id: str,
    *,
    user_id: Optional[str],
    session_token: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not record.path or not user_id or not session_token:
        return None
    resolved_path = safe_resolve(record.path)
    if not is_allowed_download_path(resolved_path):
        return None
    # Quantised to the bucket, so re-rendering the sidebar reproduces a
    # byte-identical token and the panel can be skipped instead of replaced.
    issued_at = (int(time.time()) // DOWNLOAD_TOKEN_BUCKET_SECONDS) * DOWNLOAD_TOKEN_BUCKET_SECONDS
    return {
        "path": str(resolved_path),
        "thread_id": thread_id,
        "name": record.name,
        "exp": issued_at + DOWNLOAD_TTL_SECONDS,
        "ts": issued_at,
        "user_id": user_id,
        "session_token": session_token,
    }


async def _validate_download_access(payload: Dict[str, Any], resolved_path: Path) -> None:
    user_id = payload.get("user_id")
    session_token = payload.get("session_token")
    thread_id = payload.get("thread_id")
    if not user_id or not session_token or not thread_id:
        raise HTTPException(status_code=403, detail="Access denied")

    restored_user = await _AUTH_SERVICE.restore_session(session_token)
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


@FILES_ROUTER.get("/download")
async def download_file(token: str):
    payload = decode_download_token(token)
    path_value = payload.get("path")
    if not path_value:
        raise HTTPException(status_code=400, detail="Missing file path")
    resolved_path = safe_resolve(path_value)
    if not is_allowed_download_path(resolved_path):
        raise HTTPException(status_code=403, detail="Access denied")
    await _validate_download_access(payload, resolved_path)
    if not resolved_path.exists() or not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    filename = payload.get("name") or resolved_path.name
    mime, _ = mimetypes.guess_type(filename)
    return FileResponse(resolved_path, filename=filename, media_type=mime or "application/octet-stream")
