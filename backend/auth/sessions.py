"""Session helpers for DB-backed Gradio authentication."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


class SessionManager:
    def __init__(self, *, refresh_ttl_days: int) -> None:
        self.refresh_delta = timedelta(days=refresh_ttl_days)

    @staticmethod
    def new_session_token() -> str:
        return str(uuid4())

    def refresh_expiration(self) -> datetime:
        return datetime.now(timezone.utc) + self.refresh_delta
