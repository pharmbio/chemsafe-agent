"""Partner metadata shared across the UI."""

from __future__ import annotations

from typing import Dict, List

PARTNER_ORGANIZATIONS: List[Dict[str, str]] = [
    {
        "name": "Uppsala University",
        "logo": "images/uu_logo.png",
        "url": "https://www.uu.se/en/",
        "size": "xl",
    },
    {
        "name": "PARC",
        "logo": "images/parc_logo.png",
        "url": "https://www.eu-parc.eu",
        "size": "xl",
    },
        {
        "name": "Merck Life Science",
        "logo": "images/merck_logo.png",
        "url": "https://www.merck.com",
        "size": "xl",
    },
        {
        "name": "SciLifeLab Serve",
        "logo": "images/serve_logo.png",
        "url": "https://serve.scilifelab.se/",
        "size": "xl",
    },
]


def get_partner_organizations() -> List[Dict[str, str]]:
    """Return a shallow copy of partner metadata."""
    return [dict(partner) for partner in PARTNER_ORGANIZATIONS]
