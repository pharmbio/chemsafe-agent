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
# Read-only model assets (e.g. the RiskMix THS pickles under models/ths_models).
# Deliberately not one of the per-conversation managed roots in python_executor:
# these are shared reference data, readable like the rest of the repo.
MODELS_ROOT = Path(os.environ.get("MODELS_ROOT", PERSISTENCE_ROOT / "models")).resolve()

for directory in (
    PERSISTENCE_ROOT,
    DATA_ROOT,
    RESULTS_ROOT,
    MEMORY_ROOT,
    MODELS_ROOT,
):
    directory.mkdir(parents=True, exist_ok=True)

POSTGRES_POOL_MIN_SIZE = 2
POSTGRES_POOL_MAX_SIZE = 10
POSTGRES_POOL_TIMEOUT = 30

DEFAULT_USER_ID = os.environ.get("CHEMSAFE_DEFAULT_USER_ID", "local-user")
DEFAULT_CONVERSATION_TITLE = os.environ.get(
    "CHEMSAFE_DEFAULT_CONVERSATION_TITLE",
    "New conversation",
)
AUTH_PEPPER = os.environ.get("AUTH_PEPPER", "")
AUTH_REFRESH_EXPIRES_DAYS = 7


def _download_token_secret() -> bytes:
    """Key that signs download links."""
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
GRADIO_SERVER_PORT = 7860
GRADIO_SHARE = False

PLANNING_MODEL = os.environ.get("PLANNING_MODEL", "gpt-5.4")
EXECUTE_MODEL = os.environ.get("EXECUTE_MODEL", "gpt-5.4")
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "gpt-5.4")
APPROVAL_JUDGE_MODEL = os.environ.get("APPROVAL_JUDGE_MODEL", "gpt-5.4-mini")
CONTEXT_SUMMARY_MODEL = os.environ.get("CONTEXT_SUMMARY_MODEL", "gpt-5.4-mini")
TASK_CLASSIFIER_MODEL = os.environ.get("TASK_CLASSIFIER_MODEL", "gpt-5.4-mini")

# Counts every superstep across the graph and its react sub-agents (~2 per
# model+tool exchange). 100 truncated long plan-driven runs mid-execution.
RECURSION_LIMIT = 250

# Token streaming. Coalesced before Gradio: one update per token would re-render
# the whole timeline hundreds of times a second.
STREAM_TOKENS = True
STREAM_FLUSH_SECONDS = float(os.environ.get("STREAM_FLUSH_SECONDS", "0.12"))
STREAM_FLUSH_CHARS = 180
SUMMARY_MAX_MESSAGES = 200
SUMMARY_TRIGGER_MIN_MESSAGES_FIRST = 8
SUMMARY_TRIGGER_MIN_MESSAGES = 20
SUMMARY_TRIGGER_CHAR_LIMIT = 12000
MEMORY_MAX_ITEMS = 20
MEMORY_OUTPUTS_MAX_ITEMS = 20

# Turn-anchored context
CONTEXT_KEEP_TURNS = 3
# Bounds on what the compressor itself is asked to read in one pass.
SUMMARY_SOURCE_MAX_CHARS = 120000
SUMMARY_SOURCE_MESSAGE_MAX_CHARS =  8000
# Per-turn caps for the verbatim anchors.
CONTEXT_ANCHOR_REQUEST_MAX_CHARS = 4000
CONTEXT_ANCHOR_REPORT_MAX_CHARS =  6000
CONTEXT_GOAL_MAX_CHARS = 2000
CONTEXT_ARTIFACT_MAX_ITEMS = 25
# Tool traffic inside the live run
TOOL_RESULT_MAX_CHARS = 50000
TOOL_RESULT_RECENT_FULL = 6
TOOL_RESULT_ELIDED_CHARS = 800
# Hard cap on a single python_executor result before it reaches the transcript.
PYTHON_OUTPUT_MAX_CHARS = 20000
# Above this, read_files returns a preview envelope (metadata + head + tail) rather
# than the whole file. Keep well above the largest SKILL.md; skill files are exempt.
READ_FILES_PREVIEW_THRESHOLD_CHARS =  60000
READ_FILES_PREVIEW_HEAD_LINES = 40
READ_FILES_PREVIEW_TAIL_LINES =  10
# Python interpreter sessions retained in memory, keyed by (user, conversation).
PYTHON_SESSION_CACHE_SIZE = 32
# Wall-clock ceiling for one python_executor call; without it a runaway loop hangs
# the run (LangChain runs sync tools in a thread pool, so cancelling cannot kill the
# thread). Generous: a full ECHA toxicology traversal is legitimately slow (0 = off).
PYTHON_EXEC_TIMEOUT_SECONDS = 600
# Save figures a run leaves unsaved into the output scope, so none is silently
# lost on a headless server.
FIGURE_AUTOSAVE = True

# Critic agent
CRITIC_ENABLED =  True
CRITIC_MODEL = "gpt-5.4"
CRITIC_STEPWISE_ENABLED = True
CRITIC_STEPWISE_ALWAYS = True

# Critic budgets
CRITIC_MAX_ROUNDS = 2
# Max round per step
CRITIC_STEP_MAX_ROUNDS = 2
# Step-wise mode
CRITIC_MAX_REVIEWS = 16
CRITIC_STALL_LIMIT = 2

# Gate thresholds
CRITIC_MIN_STEPS = 4
# Failed python_executor calls in a turn before review is worth it; one recovered
# failure is normal, the execute prompt encourages it.
CRITIC_FAILURE_TRIGGER = 2
# Findings carried back to the executor in pinned context.
CRITIC_FINDINGS_MAX = 8
CRITIC_FINDING_MAX_CHARS = 400

# Step-wise isolation
CRITIC_STEPWISE_ISOLATED = True
CRITIC_EVIDENCE_MAX_CHARS = 60000
CRITIC_EVIDENCE_MESSAGE_MAX_CHARS = 6000
CRITIC_RECURSION_LIMIT = 30
