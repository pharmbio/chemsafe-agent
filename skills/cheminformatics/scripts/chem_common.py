"""Shared plumbing for the cheminformatics helpers.

Two things live here that every other module depends on: the input coercion
(`to_mol`) that lets any helper accept a SMILES string, an InChI, a molblock or
an already-parsed `Chem.Mol`, and the single error type (`StructureError`) that
every helper raises when the input cannot be turned into a molecule.
"""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from rdkit import Chem, rdBase

__all__ = [
    "StructureError",
    "detect_format",
    "mute_rdkit_log",
    "resolve_output_name",
    "to_mol",
    "to_mol_or_none",
]


class StructureError(ValueError):
    """Raised when an input cannot be interpreted as a molecule.

    Subclasses `ValueError`, so a batch loop can keep using `except ValueError`
    and still catch every structural failure.
    """


_INCHIKEY_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
_MOLBLOCK_RE = re.compile(r"^\s*M\s+END\s*$|V[23]000", re.MULTILINE)

_local = threading.local()


@contextmanager
def mute_rdkit_log():
    """Silence RDKit's C++ logger for the duration of the block.

    RDKit reports sanitization failures on stderr *and* through the return
    value. Helpers that already surface the failure themselves mute the logger
    so a batch of 5,000 structures does not bury the real output.

    `BlockLogs` restores the previous state exactly, which matters because
    RDKit ships with info/debug logging off — re-enabling everything by hand
    would turn on streams that were never on.
    """
    blocker = rdBase.BlockLogs()
    try:
        yield
    finally:
        del blocker


def cached(name: str, factory: Callable[[], Any]) -> Any:
    """Thread-local singleton cache.

    RDKit's standardizer objects are expensive to build (each parses a
    transform file) and are not thread-safe, so they are cached per thread
    rather than per process.
    """
    cache = getattr(_local, "cache", None)
    if cache is None:
        cache = {}
        _local.cache = cache
    if name not in cache:
        cache[name] = factory()
    return cache[name]


def detect_format(text: str) -> str:
    """Classify a structure string: `smiles`, `inchi`, `inchikey` or `molblock`.

    The classification is syntactic — it says what the string looks like, not
    whether it parses.
    """
    if not isinstance(text, str):
        return "unknown"
    stripped = text.strip()
    if not stripped:
        return "unknown"
    if stripped.startswith("InChI="):
        return "inchi"
    if _INCHIKEY_RE.match(stripped):
        return "inchikey"
    if "\n" in stripped and _MOLBLOCK_RE.search(stripped):
        return "molblock"
    return "smiles"


def to_mol(value: Any, *, sanitize: bool = True, label: str = "input") -> Chem.Mol:
    """Coerce a `Chem.Mol`, SMILES, InChI or molblock into a `Chem.Mol`.

    Raises `StructureError` — never returns `None` — so a helper that received
    something unusable fails at the point of the mistake instead of returning a
    plausible-looking empty result. Use `parse_smiles` / `parse_molecule` when
    you want `None` back instead.
    """
    if isinstance(value, Chem.Mol):
        return value
    if not isinstance(value, str) or not value.strip():
        raise StructureError(f"{label} must be a SMILES/InChI/molblock string or an RDKit Mol, got {value!r}")

    text = value.strip()
    fmt = detect_format(text)
    with mute_rdkit_log():
        if fmt == "inchi":
            mol = Chem.MolFromInchi(text, sanitize=sanitize, removeHs=sanitize)
        elif fmt == "molblock":
            mol = Chem.MolFromMolBlock(text, sanitize=sanitize, removeHs=sanitize)
        elif fmt == "inchikey":
            raise StructureError(
                f"{label} is an InChIKey ({text}). An InChIKey is a hash of a structure, not a "
                "structure: it cannot be converted back to a molecule. Resolve it to a SMILES or "
                "InChI first."
            )
        else:
            mol = Chem.MolFromSmiles(text, sanitize=sanitize)

    if mol is None:
        raise StructureError(f"{label} could not be parsed as {fmt}: {text!r}")
    return mol


def to_mol_or_none(value: Any, *, sanitize: bool = True) -> Optional[Chem.Mol]:
    """`to_mol`, but returns `None` instead of raising."""
    try:
        return to_mol(value, sanitize=sanitize)
    except StructureError:
        return None


def mol_copy(mol: Chem.Mol) -> Chem.Mol:
    """A private copy, so a helper never mutates the caller's molecule."""
    return Chem.Mol(mol)


def resolve_output_name(output_name, *, caller: str, suffix: str) -> Path:
    """Validate a caller-supplied output path and make sure its folder exists.

    Every file a helper writes has to land in the conversation's output scope,
    which is what the injected `prepare_output_path(...)` returns. A bare
    filename would write into the process working directory, so it is rejected
    here rather than silently written somewhere the caller cannot reach.
    """
    if not isinstance(output_name, (str, Path)) or not str(output_name).strip():
        raise ValueError(
            f"{caller}: output_name must be an absolute path, got {output_name!r}. Call "
            f'{caller}(..., output_name=prepare_output_path("name{suffix}")).'
        )
    path = Path(output_name).expanduser()
    if not path.is_absolute():
        raise ValueError(
            f"{caller}: output_name must be an absolute path from prepare_output_path(...), "
            f"got {output_name!r}."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
