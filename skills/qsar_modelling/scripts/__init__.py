"""RiskMix conformal QSAR models — one module per model, one function each.

Import the function from its own module; the module and the function share a
name, so importing the module alone leaves you holding something uncallable:

    from scripts.TPO_inhibition import TPO_inhibition     # correct
    from scripts import TPO_inhibition                    # the MODULE, not the function

Each function takes a SMILES string, a comma-separated string, a list, or a
CSV/TSV path. The conformal confidence is set inside each function and differs
between models, so it is read from the result rather than passed in. Shared
machinery -- input handling, descriptors, reading the 2021 model pickles and
the conformal prediction itself -- lives in utils.py.

This file is not executed when the app merges every skill's scripts/ into one
namespace package; it only runs if this directory is imported as a package
directly. Keep it in step with the modules beside it either way.
"""

from .TRHR_antagonists import TRHR_antagonists
from .TSHR_agonist import TSHR_agonist
from .TSHR_antagonist import TSHR_antagonist
from .NIS_inhibition import NIS_inhibition
from .TPO_inhibition import TPO_inhibition
from .DIO1_inhibition import DIO1_inhibition
from .DIO2_inhibition import DIO2_inhibition
from .DIO3_inhibition import DIO3_inhibition
from .TR_beta_agonist import TR_beta_agonist
from .TR_beta_antagonist import TR_beta_antagonist
from .TTR_binding import TTR_binding
from .AHR_agonists import AHR_agonists
from .CAR_agonist import CAR_agonist
from .CAR_antagonist import CAR_antagonist
from .PXR_agonist import PXR_agonist
from .PPAR_delta_agonist import PPAR_delta_agonist
from .PPAR_delta_antagonist import PPAR_delta_antagonist
from .PPAR_gamma_agonist import PPAR_gamma_agonist
from .PPAR_gamma_antagonist import PPAR_gamma_antagonist

__all__ = [
    "TRHR_antagonists",
    "TSHR_agonist",
    "TSHR_antagonist",
    "NIS_inhibition",
    "TPO_inhibition",
    "DIO1_inhibition",
    "DIO2_inhibition",
    "DIO3_inhibition",
    "TR_beta_agonist",
    "TR_beta_antagonist",
    "TTR_binding",
    "AHR_agonists",
    "CAR_agonist",
    "CAR_antagonist",
    "PXR_agonist",
    "PPAR_delta_agonist",
    "PPAR_delta_antagonist",
    "PPAR_gamma_agonist",
    "PPAR_gamma_antagonist",
]
