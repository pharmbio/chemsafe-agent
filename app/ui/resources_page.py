from __future__ import annotations

import base64
import io
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from PIL import Image

from app.config import APP_TITLE
from app.resources import (
    ADMET_AI_COLLECTION,
    ADMET_AI_MODELS,
    CHEMINFORMATICS,
    DATABASES,
    GUIDELINE_ORGANIZATIONS,
    GUIDELINES,
    MODEL_COLLECTION,
    PREDICTIVE_MODELS,
    model_url,
)
from app.ui.assets import RESOURCES_PATH, header_links_html, inline_image_src, logo_html

# A plain server-rendered page, not a second Gradio app: the content is static.
# It must be included before the Gradio mount at "/", which matches every path.
RESOURCES_ROUTER = APIRouter()

EXTERNAL = "target='_blank' rel='noopener noreferrer'"

# Tokens and header rules mirror APP_CSS in app/ui/theme.py, so moving between the
# workspace and this page keeps the same header and palette.
RESOURCES_CSS = """
    :root {
        color-scheme: light;
        --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        --font-editorial: "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
        --page-bg: #f8f8f8;
        --surface-bg: #ffffff;
        --surface-tint: #f5f7f8;
        --text-main: #1f1f1f;
        --text-soft: #5f6368;
        --border-subtle: #d5d5d5;
        --border-strong: #bcbcbc;
        --link-color: #025e8d;
        --link-hover-color: #01486d;
        --focus-color: #fece3e;
        --header-link-divider-color: #8d8d8d;
    }
    *, *::before, *::after { box-sizing: border-box; }
    html, body {
        margin: 0;
        font-family: var(--font-ui);
        background: var(--page-bg);
        color: var(--text-main);
    }
    a { color: var(--link-color); text-decoration-thickness: 0.06em; text-underline-offset: 0.14em; }
    a:hover, a:focus { color: var(--link-hover-color); }
    a:focus-visible { outline: 3px solid var(--focus-color); outline-offset: 2px; }
    /* Matches where the workspace's Gradio padding puts its header, so the header
       does not jump when switching pages. */
    .page { padding: 2.5rem 3.25rem 3rem; }

    .app-header {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 1rem;
        margin-bottom: 0.8rem;
        padding: 0 0 0.9rem;
        border-bottom: 1px solid var(--border-strong);
    }
    .app-brand { display: flex; align-items: center; gap: 1rem; color: inherit; text-decoration: none; }
    .app-brand:hover, .app-brand:focus { color: inherit; }
    /* The workspace centres its 84px logo in a 96px column; the margin mirrors that. */
    .app-logo-img { width: 84px; height: 84px; margin: 0 6px; object-fit: contain; display: block; }
    .app-title-text {
        font-family: var(--font-editorial);
        font-size: clamp(2.75rem, 4vw, 3.5rem);
        font-weight: 700;
        letter-spacing: -0.025em;
        line-height: 0.95;
    }
    .header-links {
        margin-left: auto;
        font-weight: 600;
        font-size: 0.92rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        white-space: nowrap;
    }
    /* Gradio's prose styles override the workspace's declared link weight, size and
       colour; these are the values it actually renders. */
    .header-links-content { font-size: 0.875rem; font-weight: 400; }
    .header-links .header-link { color: #1f2937; text-decoration: none; transition: color 0.2s ease; }
    .header-links .header-link:hover, .header-links .header-link:focus { color: var(--link-color); text-decoration: underline; }
    .header-links .header-link[aria-current='page'] {
        color: var(--link-color);
        font-weight: 600;
        text-decoration: underline;
        text-decoration-thickness: 2px;
        text-underline-offset: 0.4em;
    }
    .header-links .header-link-divider { color: var(--header-link-divider-color); font-weight: 400; padding: 0 1.25rem; user-select: none; }

    .resources-intro { padding: 1.75rem 0 0.5rem; }
    .resources-intro h1 {
        font-family: var(--font-editorial);
        font-size: 2.4rem;
        letter-spacing: -0.02em;
        margin: 0 0 0.5rem;
    }
    .section-nav { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 1.25rem; }
    .section-nav a {
        padding: 0.4rem 0.85rem;
        border: 1px solid var(--border-subtle);
        border-radius: 999px;
        background: var(--surface-bg);
        color: var(--text-main);
        font-size: 0.9rem;
        font-weight: 600;
        text-decoration: none;
    }
    .section-nav a:hover, .section-nav a:focus { border-color: var(--link-color); color: var(--link-color); }

    .resource-section { padding-top: 2.5rem; scroll-margin-top: 1rem; }
    .resource-section > h2 {
        font-family: var(--font-editorial);
        font-size: 1.7rem;
        letter-spacing: -0.01em;
        margin: 0 0 1rem;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid var(--border-subtle);
    }

    /* Fixed column counts, so the four database cards span the full width at any size. */
    .card-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1rem; }
    .resource-card {
        position: relative;
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
        padding: 1.1rem 1.2rem 1.2rem;
        background: var(--surface-bg);
        border: 1px solid var(--border-subtle);
        border-radius: 10px;
        transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }
    .resource-card:hover, .resource-card:focus-within { border-color: var(--link-color); box-shadow: 0 2px 10px rgba(2, 94, 141, 0.08); }
    .resource-card .card-logo { height: 52px; display: flex; align-items: center; margin-bottom: 0.6rem; }
    .resource-card .card-logo img { max-height: 52px; max-width: 170px; object-fit: contain; }
    /* For tall logos with a tagline, which are unreadable at 52px. */
    .resource-card .card-logo--large { height: 120px; }
    .resource-card .card-logo--large img { max-height: 120px; }
    .resource-card h3 { font-size: 1.02rem; margin: 0; line-height: 1.3; }
    .resource-card h3 a { color: var(--text-main); text-decoration: none; }
    .resource-card h3 a::after { content: ""; position: absolute; inset: 0; border-radius: 10px; }
    .resource-card:hover h3 a { color: var(--link-color); }
    .resource-card .card-provider { font-size: 0.82rem; color: var(--text-soft); margin: 0; }
    .resource-card .card-description { font-size: 0.9rem; line-height: 1.5; margin: 0.35rem 0 0; }

    .panel {
        background: var(--surface-bg);
        border: 1px solid var(--border-subtle);
        border-radius: 10px;
        overflow: hidden;
    }
    .panel + .panel { margin-top: 1rem; }
    .panel-head {
        display: flex;
        align-items: center;
        gap: 1.1rem;
        padding: 1rem 1.2rem;
        background: var(--surface-tint);
        border-bottom: 1px solid var(--border-subtle);
    }
    .panel-head img { max-height: 48px; max-width: 140px; object-fit: contain; flex-shrink: 0; }
    .panel-head h3 { font-size: 1.05rem; margin: 0; }
    .panel-head p { font-size: 0.9rem; line-height: 1.5; color: var(--text-soft); margin: 0.25rem 0 0; max-width: 80ch; }

    table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
    th {
        text-align: left;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--text-soft);
        padding: 0.6rem 1.2rem;
        border-bottom: 1px solid var(--border-subtle);
    }
    td { padding: 0.6rem 1.2rem; border-bottom: 1px solid #ececec; vertical-align: top; line-height: 1.45; }
    tbody tr:last-child td { border-bottom: none; }
    tbody tr:hover td { background: #fafbfc; }
    .model-url { white-space: nowrap; }
    .model-name { white-space: nowrap; }
    .model-category { width: 13rem; white-space: nowrap; }
    .category { color: var(--text-soft); white-space: nowrap; }
    .guideline-table th:last-child, .guideline-table td.category { width: 13rem; }

    @media (max-width: 1100px) {
        .card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 900px) {
        .page { padding: 1rem 1rem 2.5rem; }
        .header-links { font-size: 0.8rem; letter-spacing: 0.06em; }
    }
    @media (max-width: 640px) {
        .card-grid { grid-template-columns: 1fr; }
        .header-links-content { font-size: 0.75rem; }
        .header-links .header-link-divider { padding: 0 0.6rem; }
        .app-logo-img { width: 56px; height: 56px; }
        .app-title-text { font-size: 2.1rem; }
        .header-links { margin-left: 0; width: 100%; }
        .resources-intro h1 { font-size: 2rem; }
        th, td { padding-left: 0.9rem; padding-right: 0.9rem; }
        /* Each row's cells stack; the last one closes the row. */
        .stacked thead { display: none; }
        .stacked td { display: block; border-bottom: none; padding-bottom: 0; }
        .stacked td + td { padding-top: 0.15rem; }
        .stacked td:last-child { width: auto; padding-bottom: 0.6rem; border-bottom: 1px solid #ececec; font-size: 0.82rem; }
        .stacked tbody tr:last-child td:last-child { border-bottom: none; }
        .model-url { white-space: normal; overflow-wrap: anywhere; }
        .panel-head { align-items: flex-start; }
    }

    /* Shown inside the workspace (see EMBED_SCRIPT): the workspace supplies the
       header, the side gutters and the background. */
    html.embedded { overflow: hidden; }
    html.embedded body { background: transparent; }
    html.embedded .page { padding: 0 0 2rem; }
"""

# For the embedded page. The workspace sizes its frame to the height posted here,
# so the frame never scrolls on its own; and since the frame has nothing to
# scroll, section links scroll the workspace instead.
EMBED_SCRIPT = """
<script>
(function () {
    function postHeight() {
        parent.postMessage(
            { type: "chemsafe:resources-height", height: Math.ceil(document.body.getBoundingClientRect().height) },
            location.origin
        );
    }
    new ResizeObserver(postHeight).observe(document.body);
    document.addEventListener("click", function (event) {
        var link = event.target.closest("a[href^='#']");
        var target = link && document.getElementById(link.getAttribute("href").slice(1));
        if (!target) return;
        event.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
})();
</script>
"""


@lru_cache(maxsize=32)
def _logo_src(path: str) -> Optional[str]:
    """The logo as a data URI with any transparent border cropped off, so a logo
    exported on a padded canvas (ECHA, RDKit) fills its slot like the others."""
    if not Path(path).exists():
        return None
    with Image.open(path) as image:
        image_format = image.format  # from the bytes: pubchem.png is really WebP
        rgba = image.convert("RGBA")
    box = rgba.getchannel("A").getbbox()
    if not box or box == (0, 0, *rgba.size):
        return inline_image_src(path)
    buffer = io.BytesIO()
    rgba.crop(box).save(buffer, format=image_format)
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{Image.MIME[image_format]};base64,{data}"


def _logo_img(path: str, css_class: str = "") -> str:
    """The logo as an inline <img>, or "" until the file exists. The organization's
    name is always printed beside it, so the image itself is decorative."""
    src = _logo_src(path)
    if not src:
        return ""
    class_attr = f" class='{css_class}'" if css_class else ""
    return f"<img{class_attr} src='{escape(src, quote=True)}' alt='' />"


def _card(entry: Dict[str, str]) -> str:
    logo = _logo_img(entry["logo"])
    size_class = " card-logo--large" if entry.get("logo_size") == "large" else ""
    logo_block = f"<div class='card-logo{size_class}'>{logo}</div>" if logo else ""
    return (
        "<article class='resource-card'>"
        f"{logo_block}"
        f"<h3><a href='{escape(entry['url'], quote=True)}' {EXTERNAL}>{escape(entry['name'])}</a></h3>"
        f"<p class='card-provider'>{escape(entry['provider'])}</p>"
        f"<p class='card-description'>{escape(entry['description'])}</p>"
        "</article>"
    )


def _section(section_id: str, title: str, body: str) -> str:
    return (
        f"<section class='resource-section' id='{section_id}' aria-labelledby='{section_id}-title'>"
        f"<h2 id='{section_id}-title'>{escape(title)}</h2>{body}</section>"
    )


def _model_link(url: str) -> str:
    return f"<a href='{escape(url, quote=True)}' {EXTERNAL}>{escape(url)}</a>"


def _model_panel(collection: Dict[str, str], columns: List[str], rows: str) -> str:
    head = "".join(f"<th scope='col'>{escape(column)}</th>" for column in columns)
    service = (
        f"<p>Model URL: <span class='model-url'>{_model_link(collection['model_url'])}</span></p>"
        if "model_url" in collection
        else ""
    )
    return (
        "<div class='panel'>"
        "<div class='panel-head'>"
        f"{_logo_img(collection['logo'])}"
        "<div>"
        f"<h3>{escape(collection['name'])}</h3>"
        f"<p>{escape(collection['description'])} "
        f"<a href='{escape(collection['url'], quote=True)}' {EXTERNAL}>Model details on GitHub</a></p>"
        f"{service}"
        "</div>"
        "</div>"
        "<table class='stacked'>"
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
        "</div>"
    )


def _models_body() -> str:
    safechem_rows = "".join(
        "<tr>"
        f"<td class='model-name'>{escape(model['name'])}</td>"
        f"<td>{escape(model['endpoint'])}</td>"
        f"<td class='model-url'>{_model_link(model_url(model['name']))}</td>"
        "</tr>"
        for model in PREDICTIVE_MODELS
    )
    admet_rows = "".join(
        "<tr>"
        f"<td class='model-category'>{escape(category)}</td>"
        f"<td>{escape(', '.join(names))}</td>"
        "</tr>"
        for category, names in ADMET_AI_MODELS.items()
    )
    return (
        _model_panel(MODEL_COLLECTION, ["Model name", "Endpoints", "Model URL"], safechem_rows)
        + _model_panel(ADMET_AI_COLLECTION, ["Category", "Model name"], admet_rows)
    )


def _guidelines_body() -> str:
    panels: List[str] = []
    for key, org in GUIDELINE_ORGANIZATIONS.items():
        # Stable sort, so documents of one category sit together in their given order.
        docs = sorted(
            (doc for doc in GUIDELINES if doc["organization"] == key),
            key=lambda doc: doc["category"],
        )
        if not docs:
            continue
        rows = "".join(
            "<tr>"
            f"<td><a href='{escape(doc['url'], quote=True)}' {EXTERNAL}>{escape(doc['title'])}</a></td>"
            f"<td class='category'>{escape(doc['category'])}</td>"
            "</tr>"
            for doc in docs
        )
        panels.append(
            "<div class='panel'>"
            "<div class='panel-head'>"
            f"{_logo_img(org['logo'])}"
            f"<h3>{escape(org['name'])}</h3>"
            "</div>"
            "<table class='guideline-table stacked'>"
            "<thead><tr><th scope='col'>Title</th><th scope='col'>Category</th></tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table>"
            "</div>"
        )
    return "".join(panels)


def _brand_html() -> str:
    title = f"<span class='app-title-text'>{escape(APP_TITLE)}</span>"
    return f"<a class='app-brand' href='/' aria-label='Back to the workspace'>{logo_html()}{title}</a>"


@lru_cache(maxsize=2)
def resources_page_html(embedded: bool = False) -> str:
    """The page, standalone or (``embedded``) as the workspace's Resources view."""
    sections = [
        ("databases", "Databases", f"<div class='card-grid'>{''.join(_card(db) for db in DATABASES)}</div>"),
        ("predictive-models", "Predictive models", _models_body()),
        ("guidelines", "Guidelines", _guidelines_body()),
        ("cheminformatics", "Cheminformatics",
         f"<div class='card-grid'>{''.join(_card(pkg) for pkg in CHEMINFORMATICS)}</div>"),
    ]
    nav = "".join(f"<a href='#{section_id}'>{escape(title)}</a>" for section_id, title, _ in sections)
    body = "".join(_section(section_id, title, html) for section_id, title, html in sections)
    header = (
        ""
        if embedded
        else "<header class='app-header'>"
        f"{_brand_html()}"
        f"<nav class='header-links' aria-label='Site'>{header_links_html('Resources')}</nav>"
        "</header>"
    )
    return (
        "<!doctype html>"
        f"<html lang='en'{' class=embedded' if embedded else ''}>"
        "<head>"
        "<meta charset='utf-8' />"
        "<meta name='viewport' content='width=device-width, initial-scale=1' />"
        f"<title>Resources | {escape(APP_TITLE)}</title>"
        f"<style>{RESOURCES_CSS}</style>"
        "</head>"
        "<body>"
        "<div class='page'>"
        f"{header}"
        "<main class='resources'>"
        "<div class='resources-intro'>"
        "<h1>Resources</h1>"
        f"<nav class='section-nav' aria-label='Sections'>{nav}</nav>"
        "</div>"
        f"{body}"
        "</main>"
        "</div>"
        f"{EMBED_SCRIPT if embedded else ''}"
        "</body>"
        "</html>"
    )


# Both spellings: the Gradio mount would otherwise answer "/resources/" itself.
@RESOURCES_ROUTER.get(RESOURCES_PATH, response_class=HTMLResponse, include_in_schema=False)
@RESOURCES_ROUTER.get(RESOURCES_PATH + "/", response_class=HTMLResponse, include_in_schema=False)
async def resources_page(embed: bool = False) -> HTMLResponse:
    return HTMLResponse(resources_page_html(embedded=embed))
