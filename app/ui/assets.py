from __future__ import annotations
import base64
import mimetypes
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import List, Optional

from app.config import APP_DESCRIPTION, APP_TITLE
from app.partners import get_partner_organizations


LOGO_PATH = "images/logo.png"
INTRO_IMAGE_PATH = "images/agent_illustration.png"
INTRO_IMAGE_ALT = f"{APP_TITLE} illustration"

HEADER_LINKS_HTML = (
    "<div class='header-links-content'>"
    "<a class='header-link' href='https://github.com/pharmbio/chemsafe-agent' target='_blank' rel='noopener noreferrer'>GitHub</a>"
    "<span class='header-link-divider' aria-hidden='true'>|</span>"
    "<a class='header-link' href='/' target='_self' rel='noopener noreferrer'>Workspace</a>"
    "</div>"
)


@lru_cache(maxsize=32)
def inline_image_src(path_value: str) -> Optional[str]:
    """Return ``path_value`` as a data URI, or None when the file is missing."""
    path = Path(path_value)
    if not path.exists():
        return None
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    mime, _ = mimetypes.guess_type(str(path))
    return f"data:{mime or 'image/png'};base64,{data}"


def logo_html() -> str:
    logo_src = inline_image_src(LOGO_PATH)
    if not logo_src:
        return ""
    return f'<img src="{logo_src}" alt="{APP_TITLE} logo" class="app-logo-img" />'


def intro_markdown() -> str:
    image_src = inline_image_src(INTRO_IMAGE_PATH)
    if not image_src:
        return APP_DESCRIPTION
    return f"![{INTRO_IMAGE_ALT}]({image_src})"


def partner_logos_html() -> str:
    cards: List[str] = []
    for org in get_partner_organizations():
        logo_src = inline_image_src(org["logo"])
        if not logo_src:
            continue
        size = (org.get("size") or "").lower()
        extra_class = " partner-logo-card--xl" if size == "xl" else ""
        cards.append(
            (
                "<a class='partner-logo-card{extra}' href='{href}' target='_blank' "
                "rel='noopener noreferrer' title='{title}'>"
                "<img src='{src}' alt='{alt}' />"
                "</a>"
            ).format(
                extra=extra_class,
                href=escape(org["url"], quote=True),
                title=escape(org["name"], quote=True),
                src=escape(logo_src, quote=True),
                alt=escape(f"{org['name']} logo", quote=True),
            )
        )
    if not cards:
        return ""
    return (
        "<div class='partner-slider' data-partner-slider='1'>"
        "<div class='partner-slider__viewport'>"
        "<div class='partner-slider__track'>{cards}</div>"
        "</div>"
        "<div class='partner-slider__dots' role='tablist' aria-label='Partner carousel controls'></div>"
        "</div>"
    ).format(cards="".join(cards))
