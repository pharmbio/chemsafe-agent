from __future__ import annotations

import hashlib
import logging
import os
import secrets
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


def _download_token_secret() -> bytes:
    """Key that signs download links.

    This used to be a constant compiled into a public repository, which let
    anyone mint a well-formed token. Forging one was still not enough to fetch a
    file — the route also re-validates the session and the thread scope — but a
    signing key that everybody has provides no defence in depth at all.

    Explicit `DOWNLOAD_TOKEN_SECRET` wins. Otherwise it is derived from
    `AUTH_PEPPER`, so every process of a multi-worker deployment agrees without
    extra configuration. With neither set it is random per process: links then
    stop working across a restart, which is harmless because they expire in ten
    minutes anyway, and is far better than a shared public key.
    """
    configured = os.environ.get("DOWNLOAD_TOKEN_SECRET")
    if configured:
        return configured.encode("utf-8")
    if AUTH_PEPPER:
        return hashlib.sha256(f"chemsafe-download:{AUTH_PEPPER}".encode("utf-8")).digest()
    logger.warning(
        "Neither DOWNLOAD_TOKEN_SECRET nor AUTH_PEPPER is set; signing download "
        "links with a per-process key. Existing links stop working on restart."
    )
    return secrets.token_bytes(32)


DOWNLOAD_TOKEN_SECRET = _download_token_secret()

GRADIO_SERVER_NAME = os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0")
GRADIO_SERVER_PORT = _int_env("GRADIO_SERVER_PORT", 7860)
GRADIO_SHARE = _bool_env("GRADIO_SHARE", False)

PLANNING_MODEL = os.environ.get("PLANNING_MODEL", "gpt-5.4")
EXECUTE_MODEL = os.environ.get("EXECUTE_MODEL", "gpt-5.4")
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "gpt-5.4")
APPROVAL_JUDGE_MODEL = os.environ.get("APPROVAL_JUDGE_MODEL", "gpt-5.4-mini")
CONTEXT_SUMMARY_MODEL = os.environ.get("CONTEXT_SUMMARY_MODEL", "gpt-5.4-mini")
TASK_CLASSIFIER_MODEL = os.environ.get("TASK_CLASSIFIER_MODEL", "gpt-5.4-mini")

# Counts every superstep across the graph and its react sub-agents, so ~2 per
# model+tool exchange. 100 truncated long plan-driven runs mid-execution.
RECURSION_LIMIT = _int_env("RECURSION_LIMIT", 250)

# Token streaming. Tokens are coalesced before reaching Gradio: one UI update
# per token would re-render the whole timeline hundreds of times a second.
STREAM_TOKENS = _bool_env("STREAM_TOKENS", True)
STREAM_FLUSH_SECONDS = float(os.environ.get("STREAM_FLUSH_SECONDS", "0.12"))
STREAM_FLUSH_CHARS = _int_env("STREAM_FLUSH_CHARS", 180)
SUMMARY_MAX_MESSAGES = _int_env("SUMMARY_MAX_MESSAGES", 200)
SUMMARY_TRIGGER_MIN_MESSAGES_FIRST = _int_env("SUMMARY_TRIGGER_MIN_MESSAGES_FIRST", 8)
SUMMARY_TRIGGER_MIN_MESSAGES = _int_env("SUMMARY_TRIGGER_MIN_MESSAGES", 20)
SUMMARY_TRIGGER_CHAR_LIMIT = _int_env("SUMMARY_TRIGGER_CHAR_LIMIT", 12000)
MEMORY_MAX_ITEMS = _int_env("MEMORY_MAX_ITEMS", 20)
MEMORY_OUTPUTS_MAX_ITEMS = _int_env("MEMORY_OUTPUTS_MAX_ITEMS", 20)

# --- Turn-anchored context ---------------------------------------------------
# How many completed exchanges survive compression verbatim. These are the
# messages a follow-up actually refers to ("redo it with the peak value"), so
# they are never handed to the summarizer.
CONTEXT_KEEP_TURNS = _int_env("CONTEXT_KEEP_TURNS", 3)
# Bounds on what the compressor itself is asked to read in one pass.
SUMMARY_SOURCE_MAX_CHARS = _int_env("SUMMARY_SOURCE_MAX_CHARS", 120000)
SUMMARY_SOURCE_MESSAGE_MAX_CHARS = _int_env("SUMMARY_SOURCE_MESSAGE_MAX_CHARS", 8000)
# Per-turn caps for the verbatim anchors.
CONTEXT_ANCHOR_REQUEST_MAX_CHARS = _int_env("CONTEXT_ANCHOR_REQUEST_MAX_CHARS", 4000)
CONTEXT_ANCHOR_REPORT_MAX_CHARS = _int_env("CONTEXT_ANCHOR_REPORT_MAX_CHARS", 6000)
CONTEXT_GOAL_MAX_CHARS = _int_env("CONTEXT_GOAL_MAX_CHARS", 2000)
CONTEXT_ARTIFACT_MAX_ITEMS = _int_env("CONTEXT_ARTIFACT_MAX_ITEMS", 25)
# Tool traffic inside the live run: the most recent results stay intact, older
# ones collapse to a stub. Nothing is removed, so tool_call pairing stays valid.
# The recent-window cap must exceed the largest SKILL.md (~37 KB), otherwise a
# freshly loaded skill would lose the instructions the agent is following.
TOOL_RESULT_MAX_CHARS = _int_env("TOOL_RESULT_MAX_CHARS", 50000)
TOOL_RESULT_RECENT_FULL = _int_env("TOOL_RESULT_RECENT_FULL", 6)
TOOL_RESULT_ELIDED_CHARS = _int_env("TOOL_RESULT_ELIDED_CHARS", 800)
# Hard cap on a single python_executor result before it reaches the transcript.
PYTHON_OUTPUT_MAX_CHARS = _int_env("PYTHON_OUTPUT_MAX_CHARS", 20000)
# Above this size read_files returns a preview envelope (metadata + head + tail +
# how to get the rest) instead of the whole file. Must stay well above the largest
# SKILL.md so loading a skill is never degraded; skill files are exempt anyway.
READ_FILES_PREVIEW_THRESHOLD_CHARS = _int_env("READ_FILES_PREVIEW_THRESHOLD_CHARS", 60000)
READ_FILES_PREVIEW_HEAD_LINES = _int_env("READ_FILES_PREVIEW_HEAD_LINES", 40)
READ_FILES_PREVIEW_TAIL_LINES = _int_env("READ_FILES_PREVIEW_TAIL_LINES", 10)
# Python interpreter sessions retained in memory, keyed by (user, conversation).
PYTHON_SESSION_CACHE_SIZE = _int_env("PYTHON_SESSION_CACHE_SIZE", 32)
# Wall-clock ceiling for one python_executor call. Without it a slow database
# fetch or a runaway loop hangs the run indefinitely: LangChain runs sync tools
# in a thread pool, so cancelling the run does not kill the thread. Generous
# because a full ECHA toxicology traversal is legitimately slow (0 disables).
PYTHON_EXEC_TIMEOUT_SECONDS = _int_env("PYTHON_EXEC_TIMEOUT_SECONDS", 600)
# Save matplotlib figures a run leaves unsaved into the conversation output
# scope, so a figure is never silently lost on a headless server.
FIGURE_AUTOSAVE = _bool_env("FIGURE_AUTOSAVE", True)
