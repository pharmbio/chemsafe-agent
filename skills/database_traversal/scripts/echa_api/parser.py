import re
import logging
from bs4 import BeautifulSoup, Tag
from .echa_client import ECHAClient

logger = logging.getLogger(__name__)

# A dossier document id is either the legacy numeric form (documents/123.html)
# or the newer "{uuid}_{uuid}" leaf id.
DOC_ID_RE = re.compile(r"((?:IUC5-)?[A-Za-z0-9-]+_[A-Za-z0-9-]+)")
_UUID_PAIR_RE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)

# Placeholder strings ECHA renders when a field carries no real value.
_EMPTY = frozenset(
    m.lower()
    for m in (
        "", "-", "—", "[Empty]", "[Not publishable]",
        "not specified", "not available", "no data",
    )
)



# Value cleaning + label/value extraction
def clean_value(text: str) -> str:
    """Collapse whitespace and drop ECHA's empty-value placeholders."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text.strip())
    return "" if text.lower() in _EMPTY else text


def extract_field_value(container: Tag, label_text: str) -> str:
    """Return the value paired with the first label matching ``label_text``.

    ECHA renders fields as sibling ``das-field_label`` / ``das-field_value``
    divs; matching is case-insensitive and substring-based.
    """
    needle = label_text.lower()
    for label_el in container.find_all(class_="das-field_label"):
        if needle in label_el.get_text(strip=True).lower():
            value_el = label_el.find_next_sibling(class_="das-field_value")
            if value_el:
                return clean_value(value_el.get_text(strip=True))
    return ""


def _extract_value(value_div) -> str:
    """Pull a display string out of one das-field_value, honouring the
    various typed widgets ECHA uses (quantities, ranges, checkboxes)."""
    range_el = value_div.find("span", class_="i6PhysicalQuantityRange")
    if range_el:
        return _extract_quantity_range(range_el)

    quantity = value_div.find("span", class_="i6PhysicalQuantity")
    if quantity:
        value = quantity.find("span", class_="value")
        unit = quantity.find("span", class_="unit")
        return clean_value(
            " ".join(part.get_text(" ", strip=True) for part in (value, unit) if part)
        )

    checked = value_div.find("span", class_="das-value_checkbox-checked")
    unchecked = value_div.find("span", class_="das-value_checkbox-unchecked")
    if checked or unchecked:
        return "checked" if checked else "unchecked"

    html_value = value_div.find("div", class_="das-field_value_html")
    if html_value:
        return clean_value(html_value.get_text(" ", strip=True))

    return clean_value(value_div.get_text(" ", strip=True))


def _extract_quantity_range(range_el) -> str:
    lower = _extract_quantity_part(range_el.find("span", class_="lower"))
    upper = _extract_quantity_part(range_el.find("span", class_="upper"))
    unit = range_el.find("span", class_="unit")
    unit_text = unit.get_text(" ", strip=True) if unit else ""

    value = f"{lower} - {upper}" if (lower and upper) else (lower or upper)
    if unit_text:
        value = f"{value} {unit_text}" if value else unit_text
    return clean_value(value)


def _extract_quantity_part(part) -> str:
    if not part:
        return ""
    qualifier = part.find("span", class_="qualifier")
    value = part.find("span", class_="value")
    return clean_value(
        " ".join(el.get_text(" ", strip=True) for el in (qualifier, value) if el)
    )


def _add_key(target: dict, key: str, value: object) -> None:
    """Insert key/value, promoting to a list when the key repeats."""
    if key not in target:
        target[key] = value
    elif isinstance(target[key], list):
        target[key].append(value)
    else:
        target[key] = [target[key], value]



# Generic dossier-document parsing (Sections 4/5/6)
def parse_document(html: str, name: str, doc_type: str, section: str) -> dict:
    """Parse one ECHA HTML document into nested label/value blocks."""
    soup = BeautifulSoup(html, "html.parser")
    h4 = soup.find("h4")
    result = {
        "name": name or (h4.get_text(" ", strip=True) if h4 else ""),
        "type": doc_type,
        "section": section,
        "fields": {},
    }

    article = soup.find("article")
    if not article:
        return result

    for block in article.find_all("section", class_=re.compile(r"das-block"), recursive=False):
        h3 = block.find("h3")
        if not h3:
            continue
        block_name = clean_value(h3.get_text(" ", strip=True))
        block_value = _extract_block(block)
        if block_name and block_value:
            _add_key(result["fields"], block_name, block_value)

    return result


def _extract_block(block) -> object:
    result = {}

    for field in block.find_all("div", class_="das-field", recursive=False):
        label = field.find("div", class_="das-field_label")
        value = field.find("div", class_="das-field_value")
        if not label or not value:
            continue
        label_text = clean_value(label.get_text(" ", strip=True))
        value_text = _extract_value(value)
        if label_text and value_text:
            _add_key(result, label_text, value_text)

    for sub_block in block.find_all("section", class_=re.compile(r"das-block"), recursive=False):
        if "das-block_repeatable" in (sub_block.get("class") or []):
            for inner in sub_block.find_all("section", class_=re.compile(r"das-block"), recursive=False):
                _append_block(result, inner)
        else:
            _append_block(result, sub_block)

    if result:
        return result

    html_value = block.find("div", class_="das-field_value_html")
    if html_value:
        return clean_value(html_value.get_text(" ", strip=True))
    value = block.find("div", class_="das-field_value")
    return _extract_value(value) if value else ""


def _append_block(result: dict, block) -> None:
    h3 = block.find("h3")
    if not h3:
        return
    key = clean_value(h3.get_text(" ", strip=True))
    value = _extract_block(block)
    if key and value:
        _add_key(result, key, value)



# Index scanning — locating document links per dossier section
def _doc_type(name: str) -> str:
    lowered = name.lower()
    return "Summary" if lowered.startswith("s-") or "summary" in lowered else "Study"


def _is_dossier_doc_name(name: str) -> bool:
    return bool(re.match(r"^(S-\d+|\d{3})\s*\|", name) or "summary" in name.lower())


def _extract_doc_id(link) -> str:
    href = str(link.get("href") or "")

    numeric = re.search(r"documents/(\d+)\.html", href)
    if numeric:
        return numeric.group(1)

    in_href = DOC_ID_RE.search(href)
    if in_href:
        return in_href.group(1)

    classes = " ".join(link.get("class") or [])
    in_class = re.search(r"das-docid-" + DOC_ID_RE.pattern, classes)
    return in_class.group(1) if in_class else ""


def _leaf_text(link) -> str:
    content = link.find("div", class_="das-link-content")
    candidates = (content or link).find_all(attrs={"data-dastttxt": True})
    tooltips = [
        clean_value(c.get("data-dastttxt", ""))
        for c in candidates
        if c.name in {"span", "div", "a"}
    ]
    for value in tooltips:
        if _is_dossier_doc_name(value):
            return value
    if tooltips:
        return tooltips[-1]
    return clean_value((content or link).get_text(" ", strip=True))


def _append_doc(section: dict, doc: dict) -> None:
    key = "summaries" if doc["type"] == "Summary" else "studies"
    section[key].append(doc)


def _extract_collapse(html: str, collapse_id: str) -> str:
    """Return the inner HTML of the ``<div class="collapse" id=...>`` block,
    tracking nesting depth so we don't stop at the first inner </div>."""
    match = re.search(
        rf'<div[^>]*class="collapse"[^>]*id="{re.escape(collapse_id)}"[^>]*>', html
    )
    if not match:
        return ""

    start = match.end()
    depth = 1
    pos = start
    while depth > 0 and pos < len(html):
        open_pos = html.find("<div", pos)
        close_pos = html.find("</div>", pos)
        if close_pos < 0:
            break
        if 0 <= open_pos < close_pos:
            depth += 1
            pos = open_pos + 4
        else:
            depth -= 1
            if depth == 0:
                return html[start:close_pos]
            pos = close_pos + 6
    return html[start:pos]


def _extract_docs_before_nested_button(html: str) -> list[dict]:
    """Collect leaf document links that appear before the first nested
    subsection button (i.e. the docs that belong directly to this level)."""
    cutoff = html.find("<button")
    fragment = html[:cutoff] if cutoff > 0 else html
    soup = BeautifulSoup(fragment, "html.parser")

    docs = []
    for link in soup.find_all("a", class_="das-leaf"):
        doc_id = _extract_doc_id(link)
        name = _leaf_text(link)
        if doc_id and _is_dossier_doc_name(name):
            docs.append({"doc_id": doc_id, "name": name, "type": _doc_type(name)})
    return docs


def _parse_collapsed_section_index(index_html: str, prefix: str, collapse_id: str) -> dict[str, dict]:
    section_html = _extract_collapse(index_html, collapse_id)
    if not section_html:
        return {}

    soup = BeautifulSoup(section_html, "html.parser")
    result: dict[str, dict] = {}

    top_docs = _extract_docs_before_nested_button(section_html)
    if top_docs:
        result[f"{prefix}.0"] = {"summaries": [], "studies": []}
        for doc in top_docs:
            _append_doc(result[f"{prefix}.0"], doc)

    for button in soup.find_all("button", class_="das-nav-header"):
        title = clean_value(button.get_text(" ", strip=True))
        match = re.match(r"^(\d+(?:\.\d+)+)\s+.+", title)
        target = (button.get("data-toc-target") or "").lstrip("#")
        if not match or not target:
            continue

        sub_html = _extract_collapse(section_html, target)
        if not sub_html:
            continue
        docs = _extract_docs_before_nested_button(sub_html)
        if not docs:
            continue

        sec_num = match.group(1)
        result[sec_num] = {"summaries": [], "studies": []}
        for doc in docs:
            _append_doc(result[sec_num], doc)

    return result


# Environmental fate (Section 5) and ecotox (Section 6) live behind these
# collapse containers in the dossier index.
_COLLAPSE_IDS = {
    "5": "id_5_Environmentalfateandpathways",
    "6": "id_6_Ecotoxicologicalinformation",
}


def parse_section_index(index_html: str, sections) -> dict[str, dict]:
    """Group Section 5/6 document links by subsection number."""
    grouped: dict[str, dict] = {}
    for sec in sections:
        collapse_id = _COLLAPSE_IDS.get(sec)
        if collapse_id:
            grouped.update(_parse_collapsed_section_index(index_html, sec, collapse_id))
    return grouped


def _section_sort_key(section: str) -> tuple[int, ...]:
    return tuple(int(part) for part in section.split(".") if part.isdigit())


def _scan_section_docs(index_html: str, section: str) -> list[dict]:
    """Find document links under a numbered section such as '2.1' or '2.3'.

    Section membership is inferred by walking up the DOM from each link and
    matching the section number in nearby text or on element id/class.
    """
    soup = BeautifulSoup(index_html, "html.parser")
    docs = []

    for link in soup.find_all("a", href=True):
        href = str(link["href"])
        numeric = re.search(r"documents/(\d+)\.html", href)
        if numeric:
            doc_id = numeric.group(1)
        else:
            uuid_pair = _UUID_PAIR_RE.match(href)
            if not uuid_pair:
                continue
            doc_id = uuid_pair.group(1)

        name = link.get_text(strip=True)
        section_normalized = section.replace(".", "_")

        # Walk up to 10 ancestors looking for the section number in nearby
        # text or on an element's id/class before accepting the link.
        current = link.parent
        matched = False
        for _ in range(10):
            if current is None:
                break
            if hasattr(current, "get_text"):
                parent_text = current.get_text(strip=True)[:200]
                if re.search(rf"(?:^|\s|\|){re.escape(section)}(?:\s|\||$)", parent_text):
                    matched = True
                    break
                el_id = current.get("id", "")
                el_class = " ".join(current.get("class", []))
                if section_normalized in el_id or section_normalized in el_class:
                    matched = True
                    break
            current = current.parent

        if not matched:
            continue

        docs.append({"doc_id": doc_id, "name": name, "type": _doc_type(name)})

    return docs



# Dossier selection
def _iter_dossier_items(data) -> list[dict]:
    return (data or {}).get("items", []) if isinstance(data, dict) else []


async def find_lead_dossiers(client: ECHAClient, substance_index: str) -> list[dict]:
    """Return lead-registrant dossiers, best (Article 10-full) first.

    Active dossiers are preferred; we only fall back to "Not active" ones when
    no active lead dossier exists.
    """
    lead_dossiers = []

    for status in ("Active", "Not active"):
        data = await client.get_dossier_list(substance_index, status=status)
        for d in _iter_dossier_items(data):
            asset_id = d.get("assetExternalId", "")
            if not asset_id:
                continue

            reach_info = d.get("reachDossierInfo", {}) or {}
            role = reach_info.get("registrationRole", "")
            if "Lead" not in role:
                continue

            lead_dossiers.append({
                "asset_id": asset_id,
                "registration_number": d.get("registrationNumber", ""),
                "subtype": reach_info.get("dossierSubtype", ""),
                "role": role,
                "status": status,
                "date": d.get("lastUpdatedDate", ""),
                "url": f"https://chem.echa.europa.eu/html-pages-prod/{asset_id}/index.html",
            })

        if lead_dossiers:
            break

    def rank(d):
        subtype = d["subtype"]
        if "Article 10" in subtype and "full" in subtype.lower():
            return 0
        if "Article 10" in subtype:
            return 1
        if "Article 18" in subtype:
            return 2
        return 3

    lead_dossiers.sort(key=rank)
    return lead_dossiers


async def select_best_dossier(client: ECHAClient, substance_index: str) -> dict | None:
    """Score dossiers and return the single best candidate for study data.

    Article 10-full outranks Article 18; a lead role adds weight. Active
    dossiers are consulted before inactive ones.
    """
    for status in ("Active", "Not active"):
        data = await client.get_dossier_list(substance_index, status=status)
        items = _iter_dossier_items(data)
        if not items:
            continue

        scored = []
        for d in items:
            asset_id = d.get("assetExternalId", "")
            if not asset_id:
                continue

            reach_info = d.get("reachDossierInfo", {}) or {}
            subtype = reach_info.get("dossierSubtype", "")
            role = reach_info.get("registrationRole", "")

            score = 0
            if "Article 10" in subtype and "full" in subtype.lower():
                score += 10
            elif "Article 10" in subtype:
                score += 5
            elif "Article 18" in subtype:
                score += 1
            if "Lead" in role:
                score += 3

            scored.append({
                "asset_id": asset_id,
                "registration_number": d.get("registrationNumber", ""),
                "subtype": subtype,
                "role": role,
                "status": status,
                "date": d.get("lastUpdatedDate", ""),
                "score": score,
            })

        if scored:
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[0]

    return None



# Section 2.1 (GHS) and 2.3 (PBT)
_HAZARD_HINT_RE = re.compile(
    r"(?:Acute|Skin|Eye|Resp\.|Muta\.|Carc\.|Repr\.|STOT|Asp\.|Aquatic|Flam)"
)


async def parse_section_2_1_ghs(client: ECHAClient, asset_id: str) -> list[dict]:
    """Parse Section 2.1 GHS classification entries from one dossier."""
    index_html = await client.get_dossier_index(asset_id)
    if not index_html:
        return []

    doc_links = _scan_section_docs(index_html, section="2.1")
    if not doc_links:
        logger.info("No Section 2.1 documents found for asset %s", asset_id)
        return []

    summaries = [d for d in doc_links if d["type"] == "Summary"]
    studies = [d for d in doc_links if d["type"] == "Study"]

    entries = []
    for doc in summaries + studies:
        html = await client.get_document_html(asset_id, doc["doc_id"])
        if not html:
            continue
        try:
            entry = _parse_ghs_document(html, doc["name"])
            if entry:
                entries.append(entry)
        except Exception as exc:
            logger.warning("Failed to parse GHS doc %s: %s", doc["doc_id"], exc)

    return entries


def _parse_ghs_document(html: str, name: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")

    entry = {
        "entry_name": name,
        "general_information": {},
        "hazard_categories": [],
        "labelling": {},
    }

    gi = entry["general_information"]
    gi["Name"] = extract_field_value(soup, "Name")
    gi["NotClassified"] = extract_field_value(soup, "Not classified")
    gi["Implementation"] = extract_field_value(soup, "Implementation")
    gi["TypeClassification"] = extract_field_value(soup, "Type of classification")
    gi["Remarks"] = extract_field_value(soup, "Remarks")
    gi["Composition"] = extract_field_value(soup, "Related composition")

    for row in soup.find_all(class_="das-field_value"):
        text = row.get_text(strip=True)
        if _HAZARD_HINT_RE.search(text):
            for cat in (c.strip() for c in text.split(",")):
                if cat and cat not in entry["hazard_categories"]:
                    entry["hazard_categories"].append(cat)

    lab = entry["labelling"]
    lab["SignalWord"] = extract_field_value(soup, "Signal word")
    lab["HazardPictogram"] = extract_field_value(soup, "Hazard pictogram")
    lab["HazardStatements"] = extract_field_value(soup, "Hazard statements")
    lab["PrecautionaryStatements"] = extract_field_value(soup, "Precautionary statements")

    return entry


async def parse_section_2_3_pbt(client: ECHAClient, asset_id: str) -> dict:
    """Parse Section 2.3 PBT / vPvB assessment documents from one dossier."""
    index_html = await client.get_dossier_index(asset_id)
    if not index_html:
        return {"summaries": [], "studies": []}

    doc_links = _scan_section_docs(index_html, section="2.3")
    if not doc_links:
        return {"summaries": [], "studies": []}

    summaries, studies = [], []
    for doc in doc_links:
        html = await client.get_document_html(asset_id, doc["doc_id"])
        if not html:
            continue
        try:
            parsed = _parse_pbt_document(html, doc["name"], doc["type"])
            (summaries if doc["type"] == "Summary" else studies).append(parsed)
        except Exception as exc:
            logger.warning("Failed to parse PBT doc %s: %s", doc["doc_id"], exc)

    return {"summaries": summaries, "studies": studies}


def _parse_pbt_document(html: str, name: str, doc_type: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    is_summary = doc_type == "Summary"

    data = {}
    if is_summary:
        data["pbt_status"] = extract_field_value(soup, "PBT status")
    else:
        data["conclusion_on_p_vp"] = extract_field_value(soup, "Conclusion on P / vP")
        data["conclusion_on_b_vb"] = extract_field_value(soup, "Conclusion on B / vB")
        data["conclusion_on_t"] = extract_field_value(soup, "Conclusion on T")

    return {("summary_name" if is_summary else "study_name"): name, "data": data}



# Sections 4/5/6 orchestration
async def parse_dossier_sections(
    client: ECHAClient,
    substance_index: str,
    sections,
    target_section: str | None = None,
    max_studies: int = 50,
) -> dict:
    """Fetch and parse the requested Section 5/6 subsections from the best dossier."""
    dossier = await select_best_dossier(client, substance_index)
    if not dossier:
        return {"error": f"No suitable dossier found for substance {substance_index}"}

    asset_id = dossier["asset_id"]
    index_html = await client.get_dossier_index(asset_id)
    if not index_html:
        return {"error": f"Could not load dossier index for {asset_id}"}

    section_docs = parse_section_index(index_html, sections)
    if target_section:
        section_docs = {
            sec: docs
            for sec, docs in section_docs.items()
            if sec == target_section or sec.startswith(f"{target_section}.")
        }

    parsed_sections = {}
    for sec_num in sorted(section_docs, key=_section_sort_key):
        docs = section_docs[sec_num]
        sec_data = {"summaries": [], "studies": []}

        for doc in docs.get("summaries", []):
            html = await client.get_document_html(asset_id, doc["doc_id"])
            if html:
                sec_data["summaries"].append(parse_document(html, doc["name"], "Summary", sec_num))

        for doc in docs.get("studies", [])[:max_studies]:
            html = await client.get_document_html(asset_id, doc["doc_id"])
            if html:
                sec_data["studies"].append(parse_document(html, doc["name"], "Study", sec_num))

        if sec_data["summaries"] or sec_data["studies"]:
            parsed_sections[sec_num] = sec_data

    return {
        "substance_index": substance_index,
        "dossier_info": {
            "asset_id": asset_id,
            "registration_number": dossier.get("registration_number", ""),
            "subtype": dossier.get("subtype", ""),
            "role": dossier.get("role", ""),
        },
        "sections": parsed_sections,
        "total_summaries": sum(len(s["summaries"]) for s in parsed_sections.values()),
        "total_studies": sum(len(s["studies"]) for s in parsed_sections.values()),
    }



# Section 7 (toxicology)
# Endpoint description text -> Section 7 subsection.
ENDPOINT_TO_SECTION = {
    "basic toxicokinetics": "7.1",
    "dermal absorption": "7.1",
    "acute toxicity: oral": "7.2",
    "acute toxicity: inhalation": "7.2",
    "acute toxicity: dermal": "7.2",
    "acute toxicity: other routes": "7.2",
    "skin irritation": "7.3",
    "eye irritation": "7.3",
    "skin sensitisation": "7.4",
    "respiratory sensitisation": "7.4",
    "repeated dose toxicity: oral": "7.5",
    "repeated dose toxicity: inhalation": "7.5",
    "repeated dose toxicity: dermal": "7.5",
    "repeated dose toxicity: other": "7.5",
    "genetic toxicity in vitro": "7.6",
    "genetic toxicity in vivo": "7.6",
    "carcinogenicity": "7.7",
    "toxicity to reproduction": "7.8",
    "developmental toxicity / teratogenicity": "7.8",
    "toxicity to reproduction: other studies": "7.8",
    "neurotoxicity": "7.9",
    "immunotoxicity": "7.9",
    "specific investigations: other studies": "7.9",
    "health surveillance data": "7.10",
    "epidemiological data": "7.10",
    "direct observations: clinical cases": "7.10",
    "exposure related observations in humans": "7.10",
}


def identify_section(endpoint_text: str) -> str:
    """Map an endpoint description to its Section 7 subsection ('7.0' if unknown)."""
    text_lower = endpoint_text.strip().lower()
    for pattern, section in ENDPOINT_TO_SECTION.items():
        if pattern in text_lower:
            return section
    return "7.0"


async def parse_section_7(
    client: ECHAClient,
    asset_id: str,
    target_section: str | None = None,
    max_studies: int = 400,
) -> dict:
    """Parse Section 7 toxicology documents from a dossier.

    Summaries are always parsed (and mined for DN(M)ELs); study parsing stops
    once ``max_studies`` records have been processed across all subsections.
    Pass ``target_section`` (e.g. '7.2') to restrict the scan.
    """
    index_html = await client.get_dossier_index(asset_id)
    if not index_html:
        return {"error": f"Could not load dossier index for {asset_id}"}

    all_docs = _scan_section7_docs(index_html)
    if target_section:
        all_docs = {k: v for k, v in all_docs.items() if k == target_section}

    result = {
        "dossier_info": {"asset_id": asset_id},
        "dnmels": [],
        "sections": {},
    }

    study_count = 0
    for section_num, docs in sorted(all_docs.items()):
        section_data = {"summaries": [], "studies": []}

        for doc in (d for d in docs if d["type"] == "Summary"):
            html = await client.get_document_html(asset_id, doc["doc_id"])
            if not html:
                continue
            try:
                soup = BeautifulSoup(html, "html.parser")
                section_data["summaries"].append(
                    _parse_toxicology_document(soup, doc["name"], "Summary", section_num)
                )
                result["dnmels"].extend(_extract_dnmels(soup))
            except Exception as exc:
                logger.warning("Failed to parse summary %s: %s", doc["doc_id"], exc)

        for doc in (d for d in docs if d["type"] == "Study"):
            if study_count >= max_studies:
                break
            html = await client.get_document_html(asset_id, doc["doc_id"])
            if not html:
                continue
            try:
                soup = BeautifulSoup(html, "html.parser")
                section_data["studies"].append(
                    _parse_toxicology_document(soup, doc["name"], "Study", section_num)
                )
                study_count += 1
            except Exception as exc:
                logger.warning("Failed to parse study %s: %s", doc["doc_id"], exc)

        result["sections"][section_num] = section_data

    return result


def _parse_toxicology_document(soup: BeautifulSoup, name: str, doc_type: str, section: str) -> dict:
    fields = {
        "endpoint": extract_field_value(soup, "Endpoint"),
        "type_of_information": extract_field_value(soup, "Type of information"),
        "adequacy": extract_field_value(soup, "Adequacy of study"),
        "reliability": extract_field_value(soup, "Reliability"),
    }

    if doc_type == "Study":
        fields["guideline"] = extract_field_value(soup, "Guideline")
        fields["qualifier"] = extract_field_value(soup, "Qualifier")
        fields["glp"] = extract_field_value(soup, "GLP compliance")
        fields["species"] = extract_field_value(soup, "Species")
        fields["strain"] = extract_field_value(soup, "Strain")
        fields["sex"] = extract_field_value(soup, "Sex")
        fields["route"] = extract_field_value(soup, "Route of administration")
        fields["vehicle"] = extract_field_value(soup, "Vehicle")
        fields["dose_descriptor"] = extract_field_value(soup, "Dose descriptor")
        fields["effect_level"] = extract_field_value(soup, "Effect level")
        fields["basis_for_effect"] = extract_field_value(soup, "Basis for effect level")
        fields["results"] = extract_field_value(soup, "Results")
        fields["conclusions"] = extract_field_value(soup, "Conclusions")
        fields["executive_summary"] = extract_field_value(soup, "Executive summary")

        if section == "7.6":  # genetic toxicity
            fields["test_type"] = extract_field_value(soup, "Test type")
            fields["metabolic_activation"] = extract_field_value(soup, "Metabolic activation")
            fields["genotoxicity"] = extract_field_value(soup, "Genotoxicity")
            fields["cytotoxicity"] = extract_field_value(soup, "Cytotoxicity")
        elif section == "7.3":  # irritation
            fields["irritation_parameter"] = extract_field_value(soup, "Parameter")
            fields["score"] = extract_field_value(soup, "Score")
        elif section == "7.4":  # sensitisation
            fields["test_system"] = extract_field_value(soup, "Test system")
    else:
        fields["key_value_for_csr"] = extract_field_value(soup, "Key value for chemical safety assessment")
        fields["discussion"] = extract_field_value(soup, "Discussion")
        fields["long_description"] = extract_field_value(soup, "Description of key information")
        fields["additional_information"] = extract_field_value(soup, "Additional information")

    return {"name": name, "type": doc_type, "section": section, "fields": fields}


# DN(M)EL fields that ECHA exposes as flat label/value pairs.
_DNEL_LABELS = tuple(
    f"DNEL ({population}, {duration}, {kind}, {route})"
    for population in ("Workers", "General population")
    for duration in ("acute", "chronic")
    for kind in ("systemic", "local")
    for route in ("inhalation", "dermal", "oral")
)


def _extract_dnmels(soup: BeautifulSoup) -> list[dict]:
    """Pull DN(M)EL values out of a summary, from both tables and flat fields."""
    dnmels = []

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]

        if not any("dnel" in h or "dmel" in h or "most sensitive" in h for h in headers):
            prev = table.find_previous()
            prev_text = prev.get_text(strip=True).lower() if prev else ""
            if not any(k in prev_text for k in ("dnel", "dmel", "derived")):
                continue

        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all("td")
            if len(cells) < 2:
                continue
            row = {
                (headers[i] if i < len(headers) else f"col_{i}"): clean_value(cell.get_text(strip=True))
                for i, cell in enumerate(cells)
            }
            if any(row.values()):
                dnmels.append(row)

    for label in _DNEL_LABELS:
        value = extract_field_value(soup, label)
        if value:
            dnmels.append({"type": label, "value": value})

    return dnmels


def _scan_section7_docs(index_html: str) -> dict[str, list[dict]]:
    """Group Section 7 document links by subsection, inferring the subsection
    from endpoint keywords in the link text or the surrounding markup."""
    soup = BeautifulSoup(index_html, "html.parser")
    sections: dict[str, list[dict]] = {}

    for link in soup.find_all("a", href=True):
        href = str(link["href"])
        numeric = re.search(r"documents/(\d+)\.html", href)
        if numeric:
            doc_id = numeric.group(1)
        else:
            uuid_pair = _UUID_PAIR_RE.match(href)
            if not uuid_pair:
                continue
            doc_id = uuid_pair.group(1)

        name = link.get_text(strip=True)
        section_num = _infer_section_from_context(link, name)
        if not section_num or not section_num.startswith("7"):
            continue

        sections.setdefault(section_num, []).append(
            {"doc_id": doc_id, "name": name, "type": _doc_type(name)}
        )

    return sections


def _infer_section_from_context(link_el, name: str) -> str | None:
    section = identify_section(name)
    if section != "7.0":
        return section

    current = link_el.parent
    for _ in range(15):
        if current is None:
            break

        text = current.get_text(strip=True)[:300] if hasattr(current, "get_text") else ""
        match = re.search(r"(7\.\d+)", text)
        if match:
            return match.group(1)

        el_id = current.get("id", "") if hasattr(current, "get") else ""
        id_match = re.search(r"7[_.-](\d+)", el_id)
        if id_match:
            return f"7.{id_match.group(1)}"

        current = current.parent

    return None



# Identifier picking
_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def select_best_cas(cas_list: list[str]) -> str:
    """Pick the first well-formed CAS number (NN...-NN-N), else the first entry."""
    for cas in cas_list:
        if _CAS_RE.match(cas.strip()):
            return cas.strip()
    return cas_list[0] if cas_list else ""



# C&L inventory classification assembly (shared by CLP + harmonised)
def unwrap_items(payload) -> list:
    """Return the ``items`` list from a C&L payload, tolerating bare lists."""
    if isinstance(payload, dict):
        return payload.get("items", [])
    return payload or []


def categories_and_hcodes(detail) -> tuple[list[str], list[str]]:
    """Extract hazard category codes and the union of H-codes from a detail payload.

    H-codes come both from the local category->H-code table and from the
    ``hazardStatements`` the API already spells out.
    """
    categories = []
    hcodes = set()
    for item in unwrap_items(detail):
        category = item.get("hazardClassAndCategoryCode", "")
        if category:
            categories.append(category)
            mapped = HAZARD_TO_HCODE.get(category)
            if mapped:
                hcodes.add(mapped)
        for statement in item.get("hazardStatements", []):
            code = statement.get("hazardStatementCode", "")
            if code:
                hcodes.add(code)
    return categories, sorted(hcodes)


def labelling_summary(labelling) -> tuple[str, list[dict]]:
    """Return (signal word(s), [{code, text}, ...]) from a labelling payload."""
    signal_words = set()
    entries = []
    for item in unwrap_items(labelling):
        signal = item.get("signalWord", {}) or {}
        if signal:
            signal_words.add(signal.get("signalWordText", ""))
        statement = item.get("hazardStatement", {}) or {}
        if statement:
            entries.append({
                "code": statement.get("hazardStatementCode", ""),
                "text": statement.get("hazardStatementText", ""),
            })
    return ", ".join(sorted(signal_words - {""})), entries


def pictogram_list(pictograms) -> list[dict]:
    return [
        {"code": p.get("code", ""), "text": p.get("text", "")}
        for p in unwrap_items(pictograms)
    ]



# GHS hazard category -> H statement code
HAZARD_TO_HCODE: dict[str, str] = {
    # Physical hazards
    "Unst. Expl.": "H200",
    "Expl. 1.1": "H201",
    "Expl. 1.2": "H202",
    "Expl. 1.3": "H203",
    "Expl. 1.4": "H204",
    "Expl. 1.5": "H205",
    "Flam. Gas 1": "H220",
    "Flam. Gas 1A": "H220",
    "Flam. Gas 1B": "H221",
    "Flam. Gas 2": "H221",
    "Pyr. Gas": "H220",
    "Aerosol 1": "H222",
    "Aerosol 2": "H223",
    "Aerosol 3": "H229",
    "Ox. Gas 1": "H270",
    "Press. Gas (Comp.)": "H280",
    "Press. Gas (Liq.)": "H280",
    "Press. Gas (Ref. Liq.)": "H281",
    "Press. Gas (Diss.)": "H280",
    "Press. Gas": "H280",
    "Flam. Liq. 1": "H224",
    "Flam. Liq. 2": "H225",
    "Flam. Liq. 3": "H226",
    "Flam. Liq. 4": "H227",
    "Flam. Sol. 1": "H228",
    "Flam. Sol. 2": "H228",
    "Self-react. A": "H240",
    "Self-react. B": "H241",
    "Self-react. C": "H242",
    "Self-react. D": "H242",
    "Self-react. E": "H242",
    "Self-react. F": "H242",
    "Pyr. Liq. 1": "H250",
    "Pyr. Sol. 1": "H250",
    "Self-heat. 1": "H251",
    "Self-heat. 2": "H252",
    "Water-react. 1": "H260",
    "Water-react. 2": "H261",
    "Water-react. 3": "H261",
    "Ox. Liq. 1": "H271",
    "Ox. Liq. 2": "H272",
    "Ox. Liq. 3": "H272",
    "Ox. Sol. 1": "H271",
    "Ox. Sol. 2": "H272",
    "Ox. Sol. 3": "H272",
    "Org. Perox. A": "H240",
    "Org. Perox. B": "H241",
    "Org. Perox. C": "H242",
    "Org. Perox. D": "H242",
    "Org. Perox. E": "H242",
    "Org. Perox. F": "H242",
    "Met. Corr. 1": "H290",
    "Desensitized Expl. 1": "H206",
    "Desensitized Expl. 2": "H207",
    "Desensitized Expl. 3": "H207",
    "Desensitized Expl. 4": "H208",
    # Health hazards
    "Acute Tox. 1 (Oral)": "H300",
    "Acute Tox. 2 (Oral)": "H300",
    "Acute Tox. 3 (Oral)": "H301",
    "Acute Tox. 4 (Oral)": "H302",
    "Acute Tox. 5 (Oral)": "H303",
    "Acute Tox. 1 (Dermal)": "H310",
    "Acute Tox. 2 (Dermal)": "H310",
    "Acute Tox. 3 (Dermal)": "H311",
    "Acute Tox. 4 (Dermal)": "H312",
    "Acute Tox. 5 (Dermal)": "H313",
    "Acute Tox. 1 (Inhalation)": "H330",
    "Acute Tox. 2 (Inhalation)": "H330",
    "Acute Tox. 3 (Inhalation)": "H331",
    "Acute Tox. 4 (Inhalation)": "H332",
    "Acute Tox. 5 (Inhalation)": "H333",
    "Skin Corr. 1": "H314",
    "Skin Corr. 1A": "H314",
    "Skin Corr. 1B": "H314",
    "Skin Corr. 1C": "H314",
    "Skin Irrit. 2": "H315",
    "Skin Irrit. 3": "H316",
    "Eye Dam. 1": "H318",
    "Eye Irrit. 2": "H319",
    "Eye Irrit. 2A": "H319",
    "Eye Irrit. 2B": "H320",
    "Resp. Sens. 1": "H334",
    "Resp. Sens. 1A": "H334",
    "Resp. Sens. 1B": "H334",
    "Skin. Sens. 1": "H317",
    "Skin. Sens. 1A": "H317",
    "Skin. Sens. 1B": "H317",
    "Muta. 1": "H340",
    "Muta. 1A": "H340",
    "Muta. 1B": "H340",
    "Muta. 2": "H341",
    "Carc. 1": "H350",
    "Carc. 1A": "H350",
    "Carc. 1B": "H350",
    "Carc. 2": "H351",
    "Repr. 1": "H360",
    "Repr. 1A": "H360",
    "Repr. 1B": "H360",
    "Repr. 2": "H361",
    "Lact.": "H362",
    "STOT SE 1": "H370",
    "STOT SE 2": "H371",
    "STOT SE 3 (respiratory irritation)": "H335",
    "STOT SE 3 (narcotic effects)": "H336",
    "STOT RE 1": "H372",
    "STOT RE 2": "H373",
    "Asp. Tox. 1": "H304",
    "Asp. Tox. 2": "H305",
    # Environmental hazards
    "Aquatic Acute 1": "H400",
    "Aquatic Acute 2": "H401",
    "Aquatic Acute 3": "H402",
    "Aquatic Chronic 1": "H410",
    "Aquatic Chronic 2": "H411",
    "Aquatic Chronic 3": "H412",
    "Aquatic Chronic 4": "H413",
    "Ozone 1": "H420",
}


def get_hcode_mapping_markdown() -> str:
    """Render the hazard-category -> H-code table as Markdown."""
    lines = [
        "# GHS Hazard Category → H-code Mapping Table",
        "",
        "| Hazard Category | H-code |",
        "|---|---|",
    ]
    lines += [f"| {category} | {hcode} |" for category, hcode in HAZARD_TO_HCODE.items()]
    return "\n".join(lines)


def get_hcode_mapping_json() -> dict[str, str]:
    return dict(HAZARD_TO_HCODE)
