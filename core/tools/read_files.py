from pathlib import Path

from langchain.tools import tool

from app.config import DATA_ROOT, MEMORY_ROOT, RESULTS_ROOT
from backend.utils.output_paths import conversation_output_root, get_current_conversation_id, get_current_user_id
from backend.utils.storage_paths import thread_data_root

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "core" / "skills"


def _get_skill_path(skill_name: str) -> Path:
    skill_dir = SKILLS_DIR / skill_name
    candidates = (
        skill_dir / "SKILL.md",
        skill_dir / f"{skill_name}.md",
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def _parse_frontmatter(markdown_text: str) -> dict[str, str]:
    lines = markdown_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata


def _resolve_file_path(file_path: str) -> Path:
    candidate = Path(file_path).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    resolved = candidate.resolve(strict=True)
    _assert_allowed_read_path(resolved)
    return resolved


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


def read_skill_metadata(skill_name: str) -> dict[str, str]:
    """Extract frontmatter metadata from a skill markdown file."""
    skill_path = _get_skill_path(skill_name)
    try:
        metadata = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "skill_name": skill_name,
            "name": skill_name,
            "description": f"Error: Skill {skill_name} does not exist.",
        }

    return {
        "skill_name": skill_name,
        "name": metadata.get("name", skill_name),
        "description": metadata.get("description", "No description provided."),
    }


def format_skill_summaries(skill_names: list[str]) -> str:
    """Render skill names and descriptions for prompt injection."""
    lines = []
    for skill_name in skill_names:
        metadata = read_skill_metadata(skill_name)
        lines.append(f'- `{metadata["skill_name"]}`: {metadata["description"]}')
    return "\n".join(lines)


@tool
def read_files(file_path: str):
    """Read a UTF-8 text file by absolute path or repository-relative path."""
    try:
        resolved_path = _resolve_file_path(file_path)
        if not resolved_path.is_file():
            return f"Error: {file_path} is not a file."
        return resolved_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"Error: File {file_path} does not exist."
    except PermissionError as exc:
        return f"Error: {exc}"
    except UnicodeDecodeError:
        return f"Error: File {file_path} is not a UTF-8 text file."
    except OSError as exc:
        return f"Error reading {file_path}: {exc}"
