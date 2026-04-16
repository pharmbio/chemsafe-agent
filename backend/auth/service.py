"""High-level authentication workflows."""

from __future__ import annotations

from uuid import UUID

from email_validator import EmailNotValidError, validate_email

from app.config import AUTH_REFRESH_EXPIRES_DAYS, AUTH_PEPPER

from .passwords import PasswordHasher
from .repository import AuthRepository, UserRecord
from .sessions import SessionManager


class AuthService:
    def __init__(self) -> None:
        self.repo = AuthRepository()
        self.hasher = PasswordHasher(pepper=AUTH_PEPPER)
        self.sessions = SessionManager(refresh_ttl_days=AUTH_REFRESH_EXPIRES_DAYS)

    async def register_user(self, email: str, password: str) -> UserRecord:
        normalized = self._validate_email(email)
        password = (password or "").strip()
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")

        existing = await self.repo.get_user_by_email(normalized)
        if existing:
            raise ValueError("Email already registered")

        hashed = self.hasher.hash(password)
        return await self.repo.create_user(normalized, hashed)

    async def login(self, email: str, password: str) -> UserRecord:
        normalized = self._validate_email(email)
        user = await self.repo.get_user_by_email(normalized)
        if not user or not self.hasher.verify(user.password_hash, password or ""):
            raise ValueError("Invalid credentials")
        if not user.is_verified:
            raise ValueError("Account pending approval")

        if self.hasher.needs_rehash(user.password_hash):
            await self.repo.revoke_user_sessions(user.id)

        await self.repo.record_login(user.id)
        return user

    async def create_session(self, user_id: UUID) -> str:
        token = self.sessions.new_session_token()
        await self.repo.create_session(
            user_id=user_id,
            session_token=token,
            expires_at=self.sessions.refresh_expiration(),
        )
        return token

    async def restore_session(self, session_token: str) -> UserRecord | None:
        if not session_token:
            return None

        session = await self.repo.get_session(session_token)
        if not session:
            return None

        user_id = session.get("user_id")
        if not user_id:
            return None

        user = await self.repo.get_user_by_id(user_id)
        if not user or not user.is_verified:
            await self.repo.revoke_session(session_token)
            return None
        return user

    async def logout(self, session_token: str | None) -> None:
        if session_token:
            await self.repo.revoke_session(session_token)

    def _validate_email(self, email: str) -> str:
        try:
            return validate_email(email, check_deliverability=False).email
        except EmailNotValidError as exc:
            raise ValueError(str(exc)) from exc
