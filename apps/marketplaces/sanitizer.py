"""Sanitizador estrito de payload de escrita com Whitelist e proteção ao vínculo da WeDrop."""
from typing import Any

from .exceptions import ForbiddenPayloadFieldError

ALLOWED_PATCH_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "attributes", "pictures", "video"}
)

FORBIDDEN_WEDROP_FIELDS: frozenset[str] = frozenset(
    {
        "price",
        "available_quantity",
        "stock",
        "variations",
        "seller_custom_field",
        "seller_sku",
    }
)

FORBIDDEN_ATTRIBUTE_IDS: frozenset[str] = frozenset(
    {"SELLER_SKU", "SKU", "SELLER_CUSTOM_FIELD"}
)


def sanitize_patch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Valida e filtra o payload de atualização garantindo que nenhum campo da WeDrop seja enviado."""
    if not isinstance(payload, dict):
        raise ValueError("O payload de patch deve ser um dicionário.")

    # 1. Bloqueio imediato caso qualquer chave proibida da WeDrop esteja presente na raiz
    forbidden_found = [
        key
        for key in payload.keys()
        if str(key).strip().lower() in FORBIDDEN_WEDROP_FIELDS
    ]
    if forbidden_found:
        raise ForbiddenPayloadFieldError(
            f"Violação de segurança WeDrop: campo(s) proibido(s) detectado(s) no payload: "
            f"{', '.join(sorted(forbidden_found))}."
        )

    sanitized: dict[str, Any] = {}

    for key, value in payload.items():
        if key not in ALLOWED_PATCH_FIELDS or value is None:
            continue

        if key == "attributes":
            if not isinstance(value, list):
                continue
            cleaned_attrs: list[dict[str, Any]] = []
            for attr in value:
                if not isinstance(attr, dict):
                    continue
                attr_id = str(attr.get("id") or "").strip()
                if not attr_id or attr_id.upper() in FORBIDDEN_ATTRIBUTE_IDS:
                    continue
                clean_item: dict[str, Any] = {"id": attr_id}
                if "value_name" in attr and attr["value_name"] is not None:
                    clean_item["value_name"] = str(attr["value_name"])
                if "value_id" in attr and attr["value_id"] is not None:
                    clean_item["value_id"] = attr["value_id"]
                cleaned_attrs.append(clean_item)
            sanitized["attributes"] = cleaned_attrs
        else:
            sanitized[key] = value

    return sanitized
