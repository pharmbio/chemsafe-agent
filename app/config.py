from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

load_dotenv(ENV_PATH)
load_dotenv()


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw)


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("chemsafe")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

APP_TITLE = os.environ.get("APP_TITLE", "ChemSafe Agent")
APP_DESCRIPTION = os.environ.get(
    "APP_DESCRIPTION",
    "Planning-first chemical safety workflow assistant with human approval and scoped outputs.",
)
DATABASE_URL = os.environ.get("DATABASE_URL")

PERSISTENCE_ROOT = Path(
    os.environ.get("PERSISTENCE_ROOT", REPO_ROOT / "persistence")
).resolve()
DATA_ROOT = Path(os.environ.get("DATA_ROOT", PERSISTENCE_ROOT / "uploaded_data")).resolve()
RESULTS_ROOT = Path(os.environ.get("RESULTS_ROOT", PERSISTENCE_ROOT / "results")).resolve()
MEMORY_ROOT = Path(os.environ.get("MEMORY_ROOT", PERSISTENCE_ROOT / "memory")).resolve()

for directory in (
    PERSISTENCE_ROOT,
    DATA_ROOT,
    RESULTS_ROOT,
    MEMORY_ROOT,
):
    directory.mkdir(parents=True, exist_ok=True)

POSTGRES_POOL_MIN_SIZE = _int_env("POSTGRES_POOL_MIN_SIZE", 2)
POSTGRES_POOL_MAX_SIZE = _int_env("POSTGRES_POOL_MAX_SIZE", 10)
POSTGRES_POOL_TIMEOUT = _int_env("POSTGRES_POOL_TIMEOUT", 30)

DEFAULT_USER_ID = os.environ.get("CHEMSAFE_DEFAULT_USER_ID", "local-user")
DEFAULT_CONVERSATION_TITLE = os.environ.get(
    "CHEMSAFE_DEFAULT_CONVERSATION_TITLE",
    "New conversation",
)
AUTH_PEPPER = os.environ.get("AUTH_PEPPER", "")
AUTH_REFRESH_EXPIRES_DAYS = _int_env("AUTH_REFRESH_EXPIRES_DAYS", 7)

GRADIO_SERVER_NAME = os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0")
GRADIO_SERVER_PORT = _int_env("GRADIO_SERVER_PORT", 7860)
GRADIO_SHARE = _bool_env("GRADIO_SHARE", False)

PLANNING_MODEL = os.environ.get("PLANNING_MODEL", "gpt-5.4-mini")
EXECUTE_MODEL = os.environ.get("EXECUTE_MODEL", "gpt-5.4-mini")
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "gpt-5.4")
APPROVAL_JUDGE_MODEL = os.environ.get("APPROVAL_JUDGE_MODEL", "gpt-5.4-mini")
CONTEXT_SUMMARY_MODEL = os.environ.get("CONTEXT_SUMMARY_MODEL", "gpt-5.4-mini")

RECURSION_LIMIT = _int_env("RECURSION_LIMIT", 100)
SUMMARY_MAX_MESSAGES = _int_env("SUMMARY_MAX_MESSAGES", 200)
SUMMARY_TRIGGER_MIN_MESSAGES_FIRST = _int_env("SUMMARY_TRIGGER_MIN_MESSAGES_FIRST", 8)
SUMMARY_TRIGGER_MIN_MESSAGES = _int_env("SUMMARY_TRIGGER_MIN_MESSAGES", 20)
SUMMARY_TRIGGER_CHAR_LIMIT = _int_env("SUMMARY_TRIGGER_CHAR_LIMIT", 12000)
MEMORY_MAX_ITEMS = _int_env("MEMORY_MAX_ITEMS", 20)
MEMORY_OUTPUTS_MAX_ITEMS = _int_env("MEMORY_OUTPUTS_MAX_ITEMS", 20)
