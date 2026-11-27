"""Estúdio de Cards Informativos 1200x1200 via Templates HTML/CSS + Playwright Headless Chromium."""
import base64
from typing import Any

from django.template.loader import render_to_string
from playwright.sync_api import sync_playwright

from apps.catalog.models import OptimizationProposal
from apps.marketplaces.sanitizer import FORBIDDEN_ATTRIBUTE_IDS

FACT_LABELS_PT: list[tuple[str, str]] = [
    ("brand", "Marca"),
    ("model", "Modelo"),
    ("material", "Material"),
    ("color", "Cor"),
    ("voltage", "Voltagem"),
    ("dimensions", "Dimensões"),
    ("weight", "Peso"),
]


def image_bytes_to_data_uri(
    image_bytes: bytes,
    mime_type: str = "image/png",
) -> str:
    """Converte bytes de imagem em Data URI base64 para embutir nos templates HTML."""
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _clean_verified_specs(proposal: OptimizationProposal) -> list[dict[str, str]]:
    """Extrai apenas atributos e fatos técnicos não nulos, bloqueando estritamente SELLER_SKU."""
    specs: list[dict[str, str]] = []
    seen_labels: set[str] = set()

    for attr in proposal.proposed_attributes_json or []:
        if not isinstance(attr, dict):
            continue
        attr_id = str(attr.get("id") or "").strip()
        if not attr_id or attr_id.upper() in FORBIDDEN_ATTRIBUTE_IDS:
            continue
        name = str(attr.get("name") or attr_id).strip()
        val = attr.get("value_name")
        if val is None:
            continue
        val_str = str(val).strip()
        if not val_str or val_str.lower() in ("none", "null", "n/a", "-"):
            continue
        key_lower = name.lower()
        if key_lower not in seen_labels:
            seen_labels.add(key_lower)
            specs.append({"label": name, "value": val_str})

    facts: dict[str, Any] = (
        proposal.extracted_facts_json
        if isinstance(proposal.extracted_facts_json, dict)
        else {}
    )
    for field_key, label_pt in FACT_LABELS_PT:
        raw_val = facts.get(field_key)
        if raw_val is None:
            continue
        val_str = str(raw_val).strip()
        if not val_str or val_str.lower() in ("none", "null", "n/a", "-"):
            continue
        if label_pt.lower() not in seen_labels:
            seen_labels.add(label_pt.lower())
            specs.append({"label": label_pt, "value": val_str})

    return specs[:6]


def _clean_included_items(proposal: OptimizationProposal) -> list[str]:
    """Retorna itens inclusos comprovados na embalagem (sem valores nulos)."""
    facts: dict[str, Any] = (
        proposal.extracted_facts_json
        if isinstance(proposal.extracted_facts_json, dict)
        else {}
    )
    raw_items = facts.get("included_items")
    if not isinstance(raw_items, list):
        return []
    cleaned: list[str] = []
    for item in raw_items:
        if item is None:
            continue
        text = str(item).strip()
        if text and text.lower() not in ("none", "null"):
            cleaned.append(text)
    return cleaned[:5]


def build_benefits_card_html(
    proposal: OptimizationProposal,
    cutout_png_bytes: bytes,
) -> str:
    """Renderiza o HTML do Card 1 (Benefícios) com recorte real e até 4 destaques validados."""
    highlights_raw = (
        proposal.card_highlights_json
        if isinstance(proposal.card_highlights_json, list)
        else []
    )
    highlights = [
        str(h).strip()
        for h in highlights_raw
        if h is not None and str(h).strip()
    ][:4]

    if not highlights:
        facts = (
            proposal.extracted_facts_json
            if isinstance(proposal.extracted_facts_json, dict)
            else {}
        )
        verified = facts.get("verified_features") or []
        highlights = [
            str(f).strip()
            for f in verified
            if f is not None and str(f).strip()
        ][:4]

    facts_dict = (
        proposal.extracted_facts_json
        if isinstance(proposal.extracted_facts_json, dict)
        else {}
    )
    product_type = str(
        facts_dict.get("product_type") or proposal.proposed_title
    ).strip()

    context = {
        "product_title": proposal.proposed_title,
        "product_type": product_type,
        "cutout_data_uri": image_bytes_to_data_uri(cutout_png_bytes, "image/png"),
        "highlights": highlights,
    }
    return render_to_string("media_studio/card_benefits.html", context)


def build_specs_card_html(
    proposal: OptimizationProposal,
    cutout_png_bytes: bytes,
) -> str:
    """Renderiza o HTML do Card 2 (Ficha Técnica & Embalagem) apenas com fatos comprovados e sem SELLER_SKU."""
    specs = _clean_verified_specs(proposal)
    included_items = _clean_included_items(proposal)
    facts_dict = (
        proposal.extracted_facts_json
        if isinstance(proposal.extracted_facts_json, dict)
        else {}
    )
    product_type = str(
        facts_dict.get("product_type") or proposal.proposed_title
    ).strip()

    context = {
        "product_title": proposal.proposed_title,
        "product_type": product_type,
        "cutout_data_uri": image_bytes_to_data_uri(cutout_png_bytes, "image/png"),
        "specs": specs,
        "included_items": included_items,
    }
    return render_to_string("media_studio/card_specs.html", context)


def render_html_to_image(
    html_content: str,
    width: int = 1200,
    height: int = 1200,
) -> bytes:
    """Renderiza uma string HTML em imagem PNG nítida usando Playwright Chromium headless."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={"width": int(width), "height": int(height)},
                device_scale_factor=1,
            )
            page.set_content(html_content, wait_until="load")
            image_bytes = page.screenshot(
                type="png",
                clip={
                    "x": 0,
                    "y": 0,
                    "width": int(width),
                    "height": int(height),
                },
            )
            return image_bytes
        finally:
            browser.close()
