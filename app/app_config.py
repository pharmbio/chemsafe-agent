from __future__ import annotations

from dataclasses import dataclass

from app.config import CRITIC_ENABLED


@dataclass(slots=True)
class AppRunConfig:
    user_request: str
    user_id: str
    conversation_id: str
    use_context_compression: bool = True
    use_critic: bool = CRITIC_ENABLED

