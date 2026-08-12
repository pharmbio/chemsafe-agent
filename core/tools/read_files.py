from pathlib import Path

from langchain.tools import tool

from app.config import DATA_ROOT, MEMORY_ROOT, RESULTS_ROOT
from backend.utils.output_paths import conversation_output_root, get_current_conversation_id, get_current_user_id
from backend.utils.skill_paths import SKILLS_DIR, find_skill_files
from backend.utils.storage_paths import thread_data_root

REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_file_path(file_path: str) -> Path:
    candidate = Path(file_path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=True)
    else:
        resolved = _resolve_relative_path(candidate).resolve(strict=True)
    _assert_allowed_read_path(resolved)
    return resolved


def _resolve_relative_path(candidate: Path) -> Path:
    """Resolve a relative path against the repository, then against skill dirs."""
    repo_candidate = REPO_ROOT / candidate
    if repo_candidate.exists():
        return repo_candidate

    matches = find_skill_files(candidate)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        owners = ", ".join(f"{match.relative_to(SKILLS_DIR).parts[0]}/{candidate}" for match in matches)
        raise ValueError(
            f"{candidate} matches several skills. Qualify it with the skill name: {owners}"
        )

    # Nothing matched; let the caller surface the usual "does not exist" error.
    return repo_candidate


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _assert_allowed_read_path(path: Path) -> None:
    resolved_path = path.resolve()
    user_id = get_current_user_id() or "anonymous-user"
    conversation_id = get_current_conversation_id() or "default-thread"
    allowed_data_root = thread_data_root(conversation_id, user_id=user_id, create=False).resolve()
    allowed_output_root = conversation_output_root(conversation_id, user_id=user_id).resolve()
    managed_roots = (DATA_ROOT.resolve(), RESULTS_ROOT.resolve(), MEMORY_ROOT.resolve())

    if _is_relative_to(resolved_path, allowed_data_root) or _is_relative_to(resolved_path, allowed_output_root):
        return

    if any(_is_relative_to(resolved_path, managed_root) for managed_root in managed_roots):
        raise PermissionError(
            "Read access to persisted data is limited to the active thread's data and output scope."
        )

    if not _is_relative_to(resolved_path, REPO_ROOT.resolve()):
        raise PermissionError("Read access is limited to repository files and the active thread scope.")



@tool
def read_files(file_path: str):
    """Read a UTF-8 text file.

    Accepts an absolute path, a repository-relative path, or a skill-relative
    path such as `references/pubchem.md` or `cheminformatics/SKILL.md`. Qualify a
    skill-relative path with the skill name (`<skill>/references/<file>.md`) when
    several skills share the same file name.
    """
    try:
        resolved_path = _resolve_file_path(file_path)
        if not resolved_path.is_file():
            return f"Error: {file_path} is not a file."
        return resolved_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"Error: File {file_path} does not exist."
    except (PermissionError, ValueError) as exc:
        return f"Error: {exc}"
    except UnicodeDecodeError:
        return f"Error: File {file_path} is not a UTF-8 text file."
    except OSError as exc:
        return f"Error reading {file_path}: {exc}"
