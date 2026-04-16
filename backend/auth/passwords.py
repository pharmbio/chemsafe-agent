"""Password hashing helpers using Argon2id."""

from __future__ import annotations

from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import VerifyMismatchError


class PasswordHasher:
    """Wrap Argon2 to include optional peppering and rehash checks."""

    def __init__(
        self,
        *,
        time_cost: int = 3,
        memory_cost: int = 64 * 1024,
        parallelism: int = 2,
        hash_len: int = 32,
        salt_len: int = 16,
        pepper: str = "",
    ) -> None:
        self._hasher = Argon2Hasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=hash_len,
            salt_len=salt_len,
        )
        self._pepper = pepper or ""

    def hash(self, password: str) -> str:
        return self._hasher.hash(self._with_pepper(password))

    def verify(self, stored_hash: str, password: str) -> bool:
        try:
            self._hasher.verify(stored_hash, self._with_pepper(password))
            return True
        except VerifyMismatchError:
            return False

    def needs_rehash(self, stored_hash: str) -> bool:
        return self._hasher.check_needs_rehash(stored_hash)

    def _with_pepper(self, password: str) -> str:
        return f"{password}{self._pepper}"
