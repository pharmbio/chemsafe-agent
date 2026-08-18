from __future__ import annotations

import builtins
import os
from pathlib import Path

from langchain.tools import tool

from app.config import (
    DATA_ROOT,
    FIGURE_AUTOSAVE,
    MEMORY_ROOT,
    PYTHON_EXEC_TIMEOUT_SECONDS,
    PYTHON_OUTPUT_MAX_CHARS,
    PYTHON_SESSION_CACHE_SIZE,
    REPO_ROOT,
    RESULTS_ROOT,
)
from backend.utils import figure_capture
from backend.utils.local_python_executor import (
    BASE_BUILTIN_MODULES,
    ExecutionTimeout,
    InterpreterError,
    local_python_executor,
    reset_executor_state,
    set_max_executor_sessions,
)
from backend.utils.output_paths import (
    conversation_output_root,
    describe_output_scope,
    get_current_conversation_id,
    get_current_user_id,
    resolve_output_folder,
    task_file_path,
)
from backend.utils.skill_paths import register_skill_scripts
from backend.utils.storage_paths import thread_data_root

# Makes every skill's helper modules importable as `scripts.<module>`, so a
# SKILL.md can reference its own scripts by short path.
register_skill_scripts()
set_max_executor_sessions(PYTHON_SESSION_CACHE_SIZE)
if FIGURE_AUTOSAVE:
    figure_capture.install()


def _session_key() -> str:
    """Interpreter namespace identity: one per user and conversation."""
    user_id = get_current_user_id() or "anonymous-user"
    conversation_id = get_current_conversation_id() or "default-thread"
    return f"{user_id}::{conversation_id}"


def _bound_output(result):
    """Keep a single tool result from dominating the transcript.

    Whatever is returned here is replayed to the model on every subsequent call
    in the run, so the middle of an oversized dump is dropped rather than the
    tail, which usually holds the answer.
    """
    if isinstance(result, str):
        text = result
    elif isinstance(result, (dict, list, tuple, set)):
        return result
    else:
        text = str(result) if result is not None else None
    if text is None or len(text) <= PYTHON_OUTPUT_MAX_CHARS:
        return result
    head = int(PYTHON_OUTPUT_MAX_CHARS * 0.6)
    tail = PYTHON_OUTPUT_MAX_CHARS - head
    omitted = len(text) - head - tail
    return (
        f"{text[:head]}\n\n"
        f"... [{omitted:,} characters omitted from the middle of this output. "
        "Re-run with narrower selection, aggregation or slicing if you need the "
        "omitted part, or write the full result to a file under the output "
        "scope and read it back in pieces.] ...\n\n"
        f"{text[-tail:]}"
    )

DEFAULT_AUTHORIZED_IMPORTS = [
    'app',
    'backend',
    'core',
    'skills',
    'scripts',
    'json',
    'pathlib',
    'sqlalchemy',
    'dotenv',
    'os',
    'sys',
    'pandas',
    'rdkit',
    'numpy',
    'matplotlib',
    'seaborn',
    'scipy',
    'sklearn',
    'fuzzywuzzy',
    'Bio',
    'posixpath',
    'ntpath',
    'pybel',
    'requests',
    'openpyxl',
    'httpx',
    'pubchempy',
    'chembl_webresource_client',
    'admet_ai',
    'deepchem',
    'core',
    'asyncio',
    'subprocess',
    'textwrap',
    "similarity_search",
]
authorized_imports = sorted(set(BASE_BUILTIN_MODULES) | set(DEFAULT_AUTHORIZED_IMPORTS))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _is_managed_persistence_path(path: Path) -> bool:
    managed_roots = (DATA_ROOT, RESULTS_ROOT, MEMORY_ROOT)
    return any(_is_relative_to(path, root) for root in managed_roots)


def _is_allowed_read_path(
    path: Path,
    *,
    repo_root: Path,
    data_root: Path,
    output_root: Path,
) -> bool:
    if _is_relative_to(path, data_root) or _is_relative_to(path, output_root):
        return True
    if _is_managed_persistence_path(path):
        return False
    return _is_relative_to(path, repo_root)


def _mode_requires_write(mode: str) -> bool:
    return any(flag in mode for flag in ("w", "a", "x", "+"))


def _build_python_execution_context() -> dict[str, object]:
    user_id = get_current_user_id() or "anonymous-user"
    conversation_id = get_current_conversation_id() or "default-thread"
    repo_root = REPO_ROOT.resolve()
    output_root = conversation_output_root(conversation_id, user_id=user_id)
    data_root = thread_data_root(conversation_id, user_id=user_id, create=False)
    root = resolve_output_folder(None, user_id=user_id, conversation_id=conversation_id)
    read_roots = (repo_root, data_root.resolve(), output_root.resolve())

    def ensure_output_dir(subfolder: str = "") -> str:
        return str(
            resolve_output_folder(
                subfolder or None,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        )

    def prepare_output_path(filename: str, subfolder: str = "") -> str:
        folder = resolve_output_folder(
            subfolder or None,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return str(
            task_file_path(
                filename,
                output_folder=folder,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        )

    def scoped_open(
        file: os.PathLike[str] | str,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
        closefd: bool = True,
        opener=None,
    ):
        if not isinstance(mode, str) or not mode:
            raise ValueError("open() mode must be a non-empty string.")
        if opener is not None:
            raise ValueError("Custom file openers are not supported in python_executor.")

        candidate = Path(os.fspath(file)).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
        elif _mode_requires_write(mode):
            resolved = (root / candidate).resolve()
        else:
            repo_candidate = (repo_root / candidate).resolve()
            output_candidate = (root / candidate).resolve()
            data_candidate = (data_root / candidate).resolve()
            if repo_candidate.exists():
                resolved = repo_candidate
            elif data_candidate.exists():
                resolved = data_candidate
            else:
                resolved = output_candidate

        if _mode_requires_write(mode):
            if not _is_relative_to(resolved, root):
                raise ValueError(
                    f"Write access is limited to the active output scope: {root}"
                )
            resolved.parent.mkdir(parents=True, exist_ok=True)
        elif not _is_allowed_read_path(
            resolved,
            repo_root=repo_root,
            data_root=data_root.resolve(),
            output_root=output_root.resolve(),
        ):
            allowed_text = ", ".join(str(path) for path in read_roots)
            raise ValueError(f"Read access is limited to: {allowed_text}")

        return builtins.open(
            resolved,
            mode=mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
            closefd=closefd,
        )

    return {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "output_root": str(root),
        "output_root_path": root,
        "output_scope": describe_output_scope(user_id=user_id, conversation_id=conversation_id),
        "ensure_output_dir": ensure_output_dir,
        "prepare_output_path": prepare_output_path,
        "open": scoped_open,
        "Path": Path,
    }


@tool
def python_executor(code: str):
    """Execute Python code safely with restricted imports.
    
    Variables defined in previous executions are preserved and available in subsequent
    executions, providing persistent state across code blocks within the session.
    The active user/conversation output scope is also injected on every call via
    `user_id`, `conversation_id`, `output_root`, `output_root_path`,
    `output_scope`, `ensure_output_dir(...)`, and `prepare_output_path(...)`.
    
    Args:
        code (str): The code to execute.
        
    Returns:
        The result of the execution.
    """
    session_key = _session_key()
    context = _build_python_execution_context()
    before_figures = figure_capture.snapshot_open_figures() if FIGURE_AUTOSAVE else ()

    try:
        result = local_python_executor(
            code,
            authorized_imports,
            variables=context,
            session_key=session_key,
            timeout=PYTHON_EXEC_TIMEOUT_SECONDS or None,
        )
    except ExecutionTimeout as exc:
        figure_capture.forget_session(session_key)
        return {
            "ok": False,
            "error_type": "ExecutionTimeout",
            "error": str(exc),
            "input_code": code,
            "recovery": (
                "The Python session was reset, so variables from before the "
                "timeout are gone. Split the work into smaller calls, narrow the "
                "query (for example a summary endpoint instead of a full "
                "traversal), pass an explicit timeout to network calls, and "
                "persist intermediate results to files under the output scope so "
                "progress survives."
            ),
        }
    except InterpreterError as exc:
        return {
            "ok": False,
            "error_type": "InterpreterError",
            "error": str(exc),
            "input_code": code,
            "authorized_imports": authorized_imports,
        }

    if FIGURE_AUTOSAVE:
        saved = figure_capture.capture_unsaved_figures(
            session_key=session_key,
            before=before_figures,
            prepare_output_path=context["prepare_output_path"],
        )
        if saved:
            listing = "\n".join(f"- {path}" for path in saved)
            note = (
                "\n\n[figures] These figures were still unsaved, so they were "
                f"written to the output scope for you:\n{listing}\n"
                "Reference these paths, or call savefig yourself to control the "
                "filename and format."
            )
            result = (result if isinstance(result, str) else str(result or "")) + note

    return _bound_output(result)


@tool
def reset_python_state():
    """Reset the Python execution state.

    This clears all variables and functions defined in previous executions,
    providing a clean slate for new code execution. Use this when you need
    to start fresh or clear accumulated state.

    Only the active conversation's interpreter is affected.

    Returns:
        str: Confirmation message.
    """
    session_key = _session_key()
    reset_executor_state(session_key)
    figure_capture.forget_session(session_key)
    return "Python execution state has been reset. All previous variables and functions have been cleared."
