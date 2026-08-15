from pathlib import Path
from typing import Optional

from langchain.tools import tool

from app.config import (
    DATA_ROOT,
    MEMORY_ROOT,
    READ_FILES_PREVIEW_HEAD_LINES,
    READ_FILES_PREVIEW_TAIL_LINES,
    READ_FILES_PREVIEW_THRESHOLD_CHARS,
    RESULTS_ROOT,
)
from backend.utils.output_paths import conversation_output_root, get_current_conversation_id, get_current_user_id
from backend.utils.skill_paths import SKILLS_DIR, find_skill_files
from backend.utils.storage_paths import thread_data_root

REPO_ROOT = Path(__file__).resolve().parents[2]

# Extensions whose content is machine-generated or record-structured. Reading one
# end to end is almost never how you get the answer out of it.
_STRUCTURED_KINDS = {
    ".csv": "CSV (delimited records)",
    ".tsv": "TSV (delimited records)",
    ".json": "JSON",
    ".jsonl": "JSON Lines (one record per line)",
    ".ndjson": "JSON Lines (one record per line)",
    ".xml": "XML",
    ".html": "HTML",
    ".htm": "HTML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".sql": "SQL dump",
    ".log": "log file",
    ".sdf": "SDF (chemical records)",
    ".smi": "SMILES list",
    ".mol": "MOL (chemical structure)",
    ".pdb": "PDB (structure)",
    ".fasta": "FASTA sequences",
    ".parquet": "Parquet (binary, columnar)",
}


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



def _describe_kind(path: Path) -> str:
    return _STRUCTURED_KINDS.get(path.suffix.lower(), "")


def _slice_lines(text: str, offset: int, limit: Optional[int]) -> str:
    """Return an explicitly requested line range, 1-indexed and inclusive."""
    lines = text.splitlines()
    total = len(lines)
    start = max(1, offset) - 1
    if start >= total:
        return (
            f"[read_files] Requested offset {max(1, offset)} is past the end of "
            f"the file ({total:,} lines)."
        )
    end = total if limit is None else min(total, start + max(1, limit))
    body = "\n".join(lines[start:end])
    header = (
        f"[read_files] Lines {start + 1:,}-{end:,} of {total:,}."
        + ("" if end >= total else f" Continue with offset={end + 1}.")
    )
    return f"{header}\n\n{body}"


def _preview(path: Path, display_path: str, text: str) -> str:
    """Envelope for a file too large to hand over whole.

    States what the file is and how to get the rest, so the agent picks an access
    method deliberately instead of either flying blind or pulling megabytes of
    records into the transcript.
    """
    lines = text.splitlines()
    total = len(lines)
    head = lines[:READ_FILES_PREVIEW_HEAD_LINES]
    tail = lines[-READ_FILES_PREVIEW_TAIL_LINES:] if total > len(head) else []
    kind = _describe_kind(path)

    parts = [
        f"[read_files preview] {display_path}",
        f"Size: {len(text):,} characters · {total:,} lines"
        + (f" · looks like {kind}" if kind else ""),
        (
            f"Previewed rather than returned whole because it exceeds "
            f"{READ_FILES_PREVIEW_THRESHOLD_CHARS:,} characters."
        ),
        "",
        "To get what you actually need from it:",
        "- Derive the answer with code in `python_executor` (parse, filter, "
        "aggregate, then report the result or write it to a file). This is "
        "usually right for record-structured files and for anything feeding a "
        "report, table or figure — see the `data_inspection` skill.",
        "- Read one region closely with "
        "`read_files(file_path, offset=<first line>, limit=<line count>)`, which "
        "returns that range exactly and is not previewed.",
        "",
        f"--- first {len(head):,} lines ---",
        "\n".join(head),
    ]
    if tail:
        parts += [f"--- last {len(tail):,} lines (of {total:,}) ---", "\n".join(tail)]
    return "\n".join(parts)


@tool
def read_files(file_path: str, offset: int = 0, limit: Optional[int] = None):
    """Read a UTF-8 text file, optionally a specific line range.

    Accepts an absolute path, a repository-relative path, or a skill-relative
    path such as `references/pubchem.md` or `cheminformatics/SKILL.md`. Qualify a
    skill-relative path with the skill name (`<skill>/references/<file>.md`) when
    several skills share the same file name.

    Args:
        file_path: Path to read.
        offset: First line to return, 1-indexed. Omit to start at the beginning.
        limit: How many lines to return from `offset`. Omit for the rest.

    Large files are returned as a preview (metadata, head and tail) rather than
    in full; pass `offset`/`limit` to read any region exactly, or parse the file
    with `python_executor` when the answer depends on its contents as a whole.
    Skill files are always returned in full.
    """
    try:
        resolved_path = _resolve_file_path(file_path)
        if not resolved_path.is_file():
            return f"Error: {file_path} is not a file."
        text = resolved_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"Error: File {file_path} does not exist."
    except (PermissionError, ValueError) as exc:
        return f"Error: {exc}"
    except UnicodeDecodeError:
        kind = _describe_kind(Path(file_path))
        hint = (
            f" It looks like {kind}; open it with `python_executor` using a "
            "library that understands the format."
            if kind
            else " Read it with `python_executor` if it is a binary format."
        )
        return f"Error: File {file_path} is not a UTF-8 text file.{hint}"
    except OSError as exc:
        return f"Error reading {file_path}: {exc}"

    # An explicit range is always honoured verbatim.
    if offset or limit is not None:
        return _slice_lines(text, offset, limit)

    # Skill instructions must arrive intact, whatever their size.
    if _is_relative_to(resolved_path, SKILLS_DIR):
        return text

    if len(text) > READ_FILES_PREVIEW_THRESHOLD_CHARS:
        return _preview(resolved_path, file_path, text)
    return text
