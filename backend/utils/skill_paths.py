"""Skill-relative short paths.

Skills live in ``skills/<name>/`` and carry two kinds of companion files:
``references/*.md`` (read with ``read_files``) and ``scripts/*.py`` (imported in
``python_executor``). A SKILL.md refers to its own companions by short path —
``references/pubchem.md``, ``from scripts.pubchem import ...`` — instead of
repeating its own directory, so skill folders stay portable.

Two helpers back that:

* ``find_skill_files`` resolves a short doc path against every skill directory.
* ``register_skill_scripts`` exposes all ``skills/*/scripts/`` directories as one
  ``scripts`` namespace package.

Because ``scripts`` is a single flat namespace, module names must be unique
across skills; ``register_skill_scripts`` fails loudly rather than let one skill
silently shadow another.
"""

from __future__ import annotations

import sys
import types
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

SCRIPTS_ALIAS = "scripts"
_ALIAS_MARKER = "__chemsafe_skill_scripts__"


def skill_dirs() -> list[Path]:
    """Every skill directory under ``skills/``, sorted by name."""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        path
        for path in SKILLS_DIR.iterdir()
        if path.is_dir() and not path.name.startswith((".", "_"))
    )


def skill_path(skill_name: str) -> Path:
    """Directory of a single skill (not checked for existence)."""
    return SKILLS_DIR / skill_name


def find_skill_files(relative_path: str | Path) -> list[Path]:
    """Resolve a skill-relative path to the files that match it.

    Accepts a skill-qualified path (``database_traversal/references/pubchem.md``)
    or a bare skill-relative path (``references/pubchem.md``), which is looked up
    in every skill directory. Returns all matches so callers can report an
    ambiguous short path instead of guessing which skill was meant.
    """
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        return []

    qualified = SKILLS_DIR / relative
    if qualified.is_file():
        return [qualified]

    matches = []
    for skill_dir in skill_dirs():
        candidate = skill_dir / relative
        if candidate.is_file():
            matches.append(candidate)
    return matches


def _module_names(scripts_dir: Path) -> list[str]:
    names = []
    for entry in scripts_dir.iterdir():
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir() or entry.suffix == ".py":
            names.append(entry.stem)
    return names


def register_skill_scripts() -> None:
    """Make ``skills/*/scripts/`` importable as the ``scripts`` package.

    Idempotent. Raises if two skills ship the same script module name, or if an
    unrelated ``scripts`` package is already imported — either case would make
    ``from scripts.x import ...`` resolve to the wrong module.
    """
    existing = sys.modules.get(SCRIPTS_ALIAS)
    if existing is not None:
        if getattr(existing, _ALIAS_MARKER, False):
            return
        raise RuntimeError(
            "Cannot register skill scripts: an unrelated 'scripts' module is already imported."
        )

    scripts_dirs = [skill / "scripts" for skill in skill_dirs() if (skill / "scripts").is_dir()]
    duplicates = sorted(
        name
        for name, count in Counter(
            name for scripts_dir in scripts_dirs for name in _module_names(scripts_dir)
        ).items()
        if count > 1
    )
    if duplicates:
        raise RuntimeError(
            "Skill script module names must be unique across skills because they share "
            f"the 'scripts' namespace. Duplicates: {', '.join(duplicates)}"
        )

    package = types.ModuleType(SCRIPTS_ALIAS)
    package.__doc__ = "Skill helper modules, merged from skills/*/scripts/."
    package.__path__ = [str(path) for path in scripts_dirs]
    setattr(package, _ALIAS_MARKER, True)
    sys.modules[SCRIPTS_ALIAS] = package
