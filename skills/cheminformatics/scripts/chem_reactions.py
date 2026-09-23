"""Chemical reactions as structural transformations.

A reaction here is a SMARTS/SMIRKS template applied to molecules: the template
says which bonds break and form, RDKit rewrites the graph, and what comes back
is the product structure. The transformation is a rule you supply — what it
produces is only as sound as the rule and the mapping you wrote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product as _cartesian
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rdkit import Chem, DataStructs
from rdkit.Chem import rdChemReactions

from .chem_common import mol_copy, mute_rdkit_log, to_mol

__all__ = [
    "ReactionResult",
    "apply_transform",
    "check_atom_balance",
    "enumerate_library",
    "parse_reaction",
    "reaction_info",
    "reaction_similarity",
    "reaction_to_smarts",
    "remove_atom_maps",
    "run_reaction",
]


@dataclass
class ReactionResult:
    """Outcome of applying a reaction template to one set of reactants."""

    reaction_smarts: str
    product_sets: List[Tuple[str, ...]] = field(default_factory=list)
    products: List[str] = field(default_factory=list)
    num_raw_outcomes: int = 0
    num_unsanitizable: int = 0
    truncated: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def reacted(self) -> bool:
        """False when the template did not match the reactants at all."""
        return bool(self.product_sets)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reaction_smarts": self.reaction_smarts,
            "product_sets": [list(item) for item in self.product_sets],
            "products": list(self.products),
            "num_raw_outcomes": self.num_raw_outcomes,
            "num_unsanitizable": self.num_unsanitizable,
            "truncated": self.truncated,
            "notes": list(self.notes),
        }


def parse_reaction(
    reaction: Any,
    *,
    use_smiles: bool = False,
    sanitize: bool = True,
) -> Optional[rdChemReactions.ChemicalReaction]:
    """Compile reaction SMARTS (or reaction SMILES) into a reaction object.

    Returns `None` when it does not compile. Pass `use_smiles=True` for a
    reaction written as SMILES (`CCO.CC(=O)O>>CCOC(C)=O`) rather than as a
    mapped template (`[C:1](=[O:2])[OH].[N!H0:3]>>[C:1](=[O:2])[N:3]`); the two
    are parsed by different rules and mixing them up is the usual reason a
    template silently matches nothing.
    """
    if isinstance(reaction, rdChemReactions.ChemicalReaction):
        return reaction
    if not isinstance(reaction, str) or not reaction.strip():
        return None
    with mute_rdkit_log():
        try:
            rxn = rdChemReactions.ReactionFromSmarts(reaction.strip(), useSmiles=use_smiles)
        except Exception:
            return None
        if rxn is None:
            return None
        try:
            if sanitize:
                rdChemReactions.SanitizeRxn(rxn)
            rxn.Initialize()
        except Exception:
            return None
    return rxn


def _require_reaction(reaction: Any, **kwargs) -> rdChemReactions.ChemicalReaction:
    rxn = parse_reaction(reaction, **kwargs)
    if rxn is None:
        raise ValueError(f"reaction template did not compile: {reaction!r}")
    return rxn


def reaction_to_smarts(reaction: Any) -> str:
    """The reaction's SMARTS, as RDKit holds it."""
    return rdChemReactions.ReactionToSmarts(_require_reaction(reaction))


def reaction_info(reaction: Any, *, use_smiles: bool = False) -> Dict[str, Any]:
    """Describe and validate a reaction template before running it.

    Keys: `smarts`, `num_reactant_templates`, `num_product_templates`,
    `num_agent_templates`, `num_warnings`, `num_errors`, `is_mapped`
    (whether atom maps link reactant and product atoms), `unmapped_product_atoms`.

    An unmapped template still "runs", but every product atom it cannot trace
    back to a reactant is invented by the template rather than transformed.
    """
    rxn = _require_reaction(reaction, use_smiles=use_smiles)
    with mute_rdkit_log():
        warnings, errors = rxn.Validate(silent=True)
    reactant_maps = {
        atom.GetAtomMapNum()
        for template in rxn.GetReactants()
        for atom in template.GetAtoms()
        if atom.GetAtomMapNum()
    }
    unmapped = sum(
        1
        for template in rxn.GetProducts()
        for atom in template.GetAtoms()
        if atom.GetAtomMapNum() not in reactant_maps
    )
    return {
        "smarts": rdChemReactions.ReactionToSmarts(rxn),
        "num_reactant_templates": rxn.GetNumReactantTemplates(),
        "num_product_templates": rxn.GetNumProductTemplates(),
        "num_agent_templates": rxn.GetNumAgentTemplates(),
        "num_warnings": warnings,
        "num_errors": errors,
        "is_mapped": bool(reactant_maps),
        "unmapped_product_atoms": unmapped,
    }


def _finalize_product(mol: Chem.Mol, clear_atom_maps: bool) -> Optional[str]:
    """Sanitize one raw product and return its canonical SMILES, or None."""
    product = mol_copy(mol)
    if clear_atom_maps:
        for atom in product.GetAtoms():
            atom.SetAtomMapNum(0)
    try:
        product.UpdatePropertyCache(strict=False)
        Chem.SanitizeMol(product)
    except Exception:
        return None
    return Chem.MolToSmiles(product)


def run_reaction(
    reaction: Any,
    reactants: Any,
    *,
    max_products: int = 1000,
    clear_atom_maps: bool = True,
    use_smiles: bool = False,
) -> ReactionResult:
    """Apply a reaction template to one set of reactants.

    `reactants` is a single structure or a sequence of them, in the order of
    the template's reactant templates. Each raw outcome is sanitized before it
    is reported; outcomes that cannot be sanitized (a template that produces an
    impossible valence) are counted in `num_unsanitizable`, not silently
    dropped.

    `product_sets` keeps each outcome as a tuple in product-template order;
    `products` is the deduplicated flat list. An empty result means the
    template did not match — check `reaction_info` before assuming it did.
    """
    rxn = _require_reaction(reaction, use_smiles=use_smiles)
    if isinstance(reactants, (str, Chem.Mol)):
        reactants = [reactants]
    mols = tuple(to_mol(item, label=f"reactants[{i}]") for i, item in enumerate(reactants))

    expected = rxn.GetNumReactantTemplates()
    if len(mols) != expected:
        raise ValueError(
            f"reaction expects {expected} reactant(s), got {len(mols)}. Pass them as a list in "
            "the order of the template's reactant templates."
        )

    result = ReactionResult(reaction_smarts=rdChemReactions.ReactionToSmarts(rxn))
    with mute_rdkit_log():
        outcomes = rxn.RunReactants(mols, maxProducts=max_products)
    result.num_raw_outcomes = len(outcomes)
    if len(outcomes) >= max_products:
        result.truncated = True
        result.notes.append(f"stopped at max_products={max_products}")

    seen_sets = set()
    seen_products = set()
    for outcome in outcomes:
        smiles_set = []
        for mol in outcome:
            smiles = _finalize_product(mol, clear_atom_maps)
            if smiles is None:
                result.num_unsanitizable += 1
                continue
            smiles_set.append(smiles)
        if not smiles_set:
            continue
        key = tuple(smiles_set)
        if key not in seen_sets:
            seen_sets.add(key)
            result.product_sets.append(key)
        for smiles in smiles_set:
            if smiles not in seen_products:
                seen_products.add(smiles)
                result.products.append(smiles)

    if result.num_unsanitizable:
        result.notes.append(
            f"{result.num_unsanitizable} raw outcome(s) failed sanitization and were not reported"
        )
    return result


def apply_transform(
    mol_or_smiles: Any,
    smirks: str,
    *,
    max_products: int = 100,
    exhaustive: bool = False,
) -> List[str]:
    """Apply a single-reactant transformation and return the unique products.

    `exhaustive=True` re-applies the transform to its own products until
    nothing changes (capped at `max_products`), which is what you want for a
    rule that can fire at several sites, such as removing every protecting
    group.
    """
    rxn = _require_reaction(smirks)
    if rxn.GetNumReactantTemplates() != 1:
        raise ValueError(
            f"apply_transform needs a single-reactant template; {smirks!r} has "
            f"{rxn.GetNumReactantTemplates()}. Use run_reaction for multi-component reactions."
        )

    start = Chem.MolToSmiles(to_mol(mol_or_smiles))
    produced: List[str] = []
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for smiles in run_reaction(rxn, current, max_products=max_products).products:
            if smiles in seen:
                continue
            seen.add(smiles)
            produced.append(smiles)
            if exhaustive and len(produced) < max_products:
                frontier.append(smiles)
    return produced


def enumerate_library(
    reaction: Any,
    reactant_lists: Sequence[Sequence[Any]],
    *,
    max_products: int = 1000,
    use_smiles: bool = False,
) -> List[Dict[str, Any]]:
    """Combinatorially enumerate a library from a reaction and reactant lists.

    One row per (reactant combination → product): `reactants`, `product`.
    Combinations the template does not match produce no row. Capped at
    `max_products` rows.
    """
    rxn = _require_reaction(reaction, use_smiles=use_smiles)
    expected = rxn.GetNumReactantTemplates()
    if len(reactant_lists) != expected:
        raise ValueError(f"reaction expects {expected} reactant list(s), got {len(reactant_lists)}")

    rows: List[Dict[str, Any]] = []
    for combination in _cartesian(*reactant_lists):
        outcome = run_reaction(rxn, list(combination))
        labels = [item if isinstance(item, str) else Chem.MolToSmiles(to_mol(item)) for item in combination]
        for smiles in outcome.products:
            rows.append({"reactants": labels, "product": smiles})
            if len(rows) >= max_products:
                return rows
    return rows


def remove_atom_maps(mol_or_smiles: Any) -> str:
    """Strip atom-map numbers, which reaction templates leave on their products."""
    mol = mol_copy(to_mol(mol_or_smiles))
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    return Chem.MolToSmiles(mol)


def reaction_similarity(a: Any, b: Any, *, kind: str = "structural") -> float:
    """Similarity between two reactions.

    `kind="structural"` fingerprints reactants and products together — close to
    1.0 for reactions on similar substrates. `kind="difference"` fingerprints
    only what changed, which is the one that groups reactions by
    *transformation type* regardless of substrate.
    """
    rxn_a, rxn_b = _require_reaction(a), _require_reaction(b)
    with mute_rdkit_log():
        if kind == "structural":
            fp_a = rdChemReactions.CreateStructuralFingerprintForReaction(rxn_a)
            fp_b = rdChemReactions.CreateStructuralFingerprintForReaction(rxn_b)
            return float(DataStructs.TanimotoSimilarity(fp_a, fp_b))
        if kind == "difference":
            fp_a = rdChemReactions.CreateDifferenceFingerprintForReaction(rxn_a)
            fp_b = rdChemReactions.CreateDifferenceFingerprintForReaction(rxn_b)
            return float(DataStructs.TanimotoSimilarity(fp_a, fp_b))
    raise ValueError(f"kind must be 'structural' or 'difference', got {kind!r}")


def check_atom_balance(reaction_smiles: str) -> Dict[str, Any]:
    """Check that a written reaction conserves atoms and charge.

    Takes a reaction SMILES (`reactants>agents>products`; agents optional) and
    compares element counts on both sides — the arithmetic check that catches a
    reaction record with a missing co-product or a stoichiometry error.

    Keys: `balanced`, `charge_balanced`, `reactant_atoms`, `product_atoms`,
    `missing_from_products`, `missing_from_reactants`, `charge_delta`.
    """
    if not isinstance(reaction_smiles, str) or ">" not in reaction_smiles:
        raise ValueError(f"expected a reaction SMILES with '>' separators, got {reaction_smiles!r}")

    parts = reaction_smiles.strip().split(">")
    if len(parts) == 2:
        left, right = parts
    elif len(parts) == 3:
        left, _agents, right = parts
    else:
        raise ValueError(f"could not split reaction SMILES into sides: {reaction_smiles!r}")

    def inventory(side: str) -> Tuple[Dict[str, int], int]:
        counts: Dict[str, int] = {}
        charge = 0
        for component in filter(None, (piece.strip() for piece in side.split("."))):
            mol = to_mol(component, label="reaction component")
            charge += Chem.GetFormalCharge(mol)
            for atom in mol.GetAtoms():
                counts[atom.GetSymbol()] = counts.get(atom.GetSymbol(), 0) + 1
                if atom.GetTotalNumHs():
                    counts["H"] = counts.get("H", 0) + atom.GetTotalNumHs()
        return counts, charge

    reactant_atoms, reactant_charge = inventory(left)
    product_atoms, product_charge = inventory(right)
    elements = set(reactant_atoms) | set(product_atoms)
    missing_from_products = {
        element: reactant_atoms.get(element, 0) - product_atoms.get(element, 0)
        for element in elements
        if reactant_atoms.get(element, 0) > product_atoms.get(element, 0)
    }
    missing_from_reactants = {
        element: product_atoms.get(element, 0) - reactant_atoms.get(element, 0)
        for element in elements
        if product_atoms.get(element, 0) > reactant_atoms.get(element, 0)
    }
    return {
        "balanced": not missing_from_products and not missing_from_reactants,
        "charge_balanced": reactant_charge == product_charge,
        "reactant_atoms": dict(sorted(reactant_atoms.items())),
        "product_atoms": dict(sorted(product_atoms.items())),
        "missing_from_products": missing_from_products,
        "missing_from_reactants": missing_from_reactants,
        "charge_delta": product_charge - reactant_charge,
    }
