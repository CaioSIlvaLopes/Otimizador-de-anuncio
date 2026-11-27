"""Pipeline sequencial dos 4 sub-agentes de IA com travas determinísticas WeDrop e Anti-Alucinação."""
import json
from typing import Any

from apps.catalog.models import (
    Listing,
    ListingSnapshot,
    ListingStatus,
    OptimizationProposal,
    ProposalStatus,
)
from apps.governance.defaults import DEFAULT_FORBIDDEN_TERMS, DEFAULT_PROMPTS
from apps.governance.models import AgentNameChoices, PromptChannelChoices, PromptConfig

from .client import GeminiStructuredClient, download_listing_images
from .schemas import (
    AttributesOutput,
    AuditorOutput,
    ExtractedFacts,
    SeoCopyOutput,
)

# IDs de atributos estritamente bloqueados para proteger o vínculo WeDrop
PROTECTED_ATTRIBUTE_IDS: set[str] = {
    "SELLER_SKU",
    "SKU",
    "SELLER_CUSTOM_FIELD",
}

ML_MAX_TITLE_LENGTH = 60
AUDITOR_PENALTY_MAX_SCORE = 60


def _resolve_prompt_config(
    agent_name: str,
    channel: str = PromptChannelChoices.MERCADO_LIVRE,
) -> tuple[str, list[str]]:
    """Retorna (system_prompt, forbidden_terms) do banco ou fallback dos defaults."""
    config = PromptConfig.get_active(agent_name=agent_name, channel=channel)
    if config is not None:
        terms = (
            list(config.forbidden_terms)
            if isinstance(config.forbidden_terms, list)
            else []
        )
        return config.system_prompt, terms

    for item in DEFAULT_PROMPTS:
        if item["agent_name"] == agent_name:
            return str(item["system_prompt"]), list(item.get("forbidden_terms", []))
    return "", list(DEFAULT_FORBIDDEN_TERMS)


def filter_editable_category_attributes(
    category_attributes_json: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove SELLER_SKU e atributos read_only ou hidden antes de enviar ao agente ATTRIBUTES."""
    if not isinstance(category_attributes_json, list):
        return []

    filtered: list[dict[str, Any]] = []
    for attr in category_attributes_json:
        if not isinstance(attr, dict):
            continue
        attr_id = str(attr.get("id") or "").strip().upper()
        if not attr_id or attr_id in PROTECTED_ATTRIBUTE_IDS:
            continue

        tags = attr.get("tags")
        is_read_only = bool(attr.get("read_only"))
        is_hidden = bool(attr.get("hidden"))

        if isinstance(tags, dict):
            is_read_only = is_read_only or bool(tags.get("read_only"))
            is_hidden = is_hidden or bool(tags.get("hidden"))
        elif isinstance(tags, (list, tuple, set)):
            tag_set = {str(t).lower() for t in tags}
            is_read_only = is_read_only or ("read_only" in tag_set)
            is_hidden = is_hidden or ("hidden" in tag_set)

        if is_read_only or is_hidden:
            continue

        filtered.append(attr)

    return filtered


def run_extractor(
    raw_item_json: dict[str, Any],
    raw_description_json: dict[str, Any],
    channel: str = PromptChannelChoices.MERCADO_LIVRE,
    client: GeminiStructuredClient | None = None,
    images: list[tuple[bytes, str]] | None = None,
) -> tuple[ExtractedFacts, dict[str, int]]:
    """Sub-Agente 1 (EXTRACTOR): extrai apenas fatos técnicos comprovados do anúncio e imagens."""
    llm_client = client or GeminiStructuredClient()
    system_prompt, _ = _resolve_prompt_config(AgentNameChoices.EXTRACTOR, channel)

    source_summary = {
        "title": raw_item_json.get("title", ""),
        "condition": raw_item_json.get("condition", ""),
        "plain_text_description": raw_description_json.get("plain_text", ""),
        "existing_attributes": [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "value_name": a.get("value_name"),
            }
            for a in (raw_item_json.get("attributes") or [])
            if isinstance(a, dict)
            and str(a.get("id") or "").upper() not in PROTECTED_ATTRIBUTE_IDS
        ],
    }
    user_prompt = (
        "Extraia os fatos técnicos estritamente comprovados deste anúncio bruto. "
        "Qualquer campo sem comprovação explícita DEVE ser retornado como null.\n\n"
        f"DADOS DA FONTE:\n{json.dumps(source_summary, ensure_ascii=False, indent=2)}"
    )
    return llm_client.generate_structured(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=ExtractedFacts,
        images=images,
    )


def run_attributes_agent(
    extracted_facts: ExtractedFacts,
    category_attributes_json: list[dict[str, Any]],
    channel: str = PromptChannelChoices.MERCADO_LIVRE,
    client: GeminiStructuredClient | None = None,
) -> tuple[AttributesOutput, dict[str, int]]:
    """Sub-Agente 2 (ATTRIBUTES): preenche ficha técnica bloqueando SELLER_SKU e read-only na entrada e saída."""
    llm_client = client or GeminiStructuredClient()
    system_prompt, _ = _resolve_prompt_config(AgentNameChoices.ATTRIBUTES, channel)

    allowed_attributes = filter_editable_category_attributes(category_attributes_json)
    allowed_ids: set[str] = {
        str(a.get("id")).strip().upper()
        for a in allowed_attributes
        if a.get("id")
    }

    compact_schema = [
        {
            "id": a.get("id"),
            "name": a.get("name"),
            "value_type": a.get("value_type"),
            "tags": a.get("tags"),
            "values": [
                v.get("name")
                for v in (a.get("values") or [])[:15]
                if isinstance(v, dict) and v.get("name")
            ],
        }
        for a in allowed_attributes
    ]

    user_prompt = (
        "Cruze os fatos extraídos (ExtractedFacts) com o schema de atributos permitidos da categoria.\n"
        "Preencha apenas atributos comprovados e JAMAIS inclua SELLER_SKU ou atributos read-only.\n\n"
        f"EXTRACTED_FACTS:\n{extracted_facts.model_dump_json(indent=2)}\n\n"
        f"CATEGORY_ATTRIBUTES_SCHEMA:\n{json.dumps(compact_schema, ensure_ascii=False, indent=2)}"
    )

    output, usage = llm_client.generate_structured(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=AttributesOutput,
    )

    # Filtro determinístico de saída: descarta SELLER_SKU e qualquer ID que não estava permitido
    sanitized_attributes = [
        attr
        for attr in output.attributes
        if attr.id.strip().upper() not in PROTECTED_ATTRIBUTE_IDS
        and (not allowed_ids or attr.id.strip().upper() in allowed_ids)
    ]
    output.attributes = sanitized_attributes
    output.skipped_required_ids = [
        req_id
        for req_id in output.skipped_required_ids
        if str(req_id).strip().upper() not in PROTECTED_ATTRIBUTE_IDS
    ]
    return output, usage


def run_seo_copy_agent(
    extracted_facts: ExtractedFacts,
    filled_attributes: AttributesOutput,
    raw_item_json: dict[str, Any],
    raw_description_json: dict[str, Any],
    sold_quantity: int = 0,
    channel: str = PromptChannelChoices.MERCADO_LIVRE,
    client: GeminiStructuredClient | None = None,
) -> tuple[SeoCopyOutput, dict[str, int]]:
    """Sub-Agente 3 (SEO_COPY): gera título (<= 60 chars no ML), descrição em texto puro, cards e vídeo."""
    llm_client = client or GeminiStructuredClient()
    system_prompt, forbidden_terms = _resolve_prompt_config(
        AgentNameChoices.SEO_COPY,
        channel,
    )

    title_lock_note = (
        f"ATENÇÃO: Este anúncio possui sold_quantity={sold_quantity} (> 0). No Mercado Livre o título "
        "ao vivo ficará bloqueado no envio final, mas ainda assim proponha um título SEO válido (<= 60 caracteres) "
        "para referência e capriche na descrição e atributos."
        if sold_quantity > 0
        else "Este anúncio possui sold_quantity=0 (título editável). O título proposto DEVE ter no máximo 60 caracteres."
    )

    user_prompt = (
        f"CANAL: {channel}\n"
        f"{title_lock_note}\n"
        f"TÍTULO ORIGINAL DO FORNECEDOR: {raw_item_json.get('title', '')}\n"
        f"DESCRIÇÃO ORIGINAL DO FORNECEDOR: {raw_description_json.get('plain_text', '')}\n"
        f"TERMOS ESTRITAMENTE PROIBIDOS: {json.dumps(forbidden_terms, ensure_ascii=False)}\n\n"
        f"FATOS COMPROVADOS (ExtractedFacts):\n{extracted_facts.model_dump_json(indent=2)}\n\n"
        f"FICHA TÉCNICA PREENCHIDA:\n{filled_attributes.model_dump_json(indent=2)}"
    )

    return llm_client.generate_structured(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=SeoCopyOutput,
    )


def run_auditor(
    extracted_facts: ExtractedFacts,
    filled_attributes: AttributesOutput,
    seo_copy: SeoCopyOutput,
    sold_quantity: int = 0,
    channel: str = PromptChannelChoices.MERCADO_LIVRE,
    client: GeminiStructuredClient | None = None,
) -> tuple[AuditorOutput, dict[str, int]]:
    """Sub-Agente 4 (AUDITOR): audita alucinações + aplica trava determinística em Python."""
    llm_client = client or GeminiStructuredClient()
    auditor_prompt, auditor_terms = _resolve_prompt_config(
        AgentNameChoices.AUDITOR,
        channel,
    )
    _, seo_terms = _resolve_prompt_config(AgentNameChoices.SEO_COPY, channel)

    # Combina termos proibidos configurados no banco (ou defaults) preservando ordem sem duplicatas
    combined_forbidden_terms: list[str] = []
    seen_lower: set[str] = set()
    for term in [*auditor_terms, *seo_terms]:
        cleaned = str(term).strip()
        if cleaned and cleaned.lower() not in seen_lower:
            seen_lower.add(cleaned.lower())
            combined_forbidden_terms.append(cleaned)

    user_prompt = (
        f"Audite a proposta de otimização para o canal {channel} (sold_quantity={sold_quantity}).\n"
        f"TERMOS PROIBIDOS: {json.dumps(combined_forbidden_terms, ensure_ascii=False)}\n\n"
        f"FATOS EXTRAÍDOS DA FONTE (ExtractedFacts):\n{extracted_facts.model_dump_json(indent=2)}\n\n"
        f"ATRIBUTOS PROPOSTOS:\n{filled_attributes.model_dump_json(indent=2)}\n\n"
        f"SEO & COPY PROPOSTOS:\n{seo_copy.model_dump_json(indent=2)}"
    )

    auditor_output, usage = llm_client.generate_structured(
        system_prompt=auditor_prompt,
        user_prompt=user_prompt,
        response_schema=AuditorOutput,
    )

    # ==========================================================================
    # TRAVA DETERMINÍSTICA EM PYTHON (Sobrepõe nota otimista do LLM se violar regras)
    # ==========================================================================
    has_deterministic_violation = False
    combined_text_lower = (
        f"{seo_copy.proposed_title}\n{seo_copy.proposed_description}".lower()
    )

    existing_found_lower = {
        t.lower() for t in auditor_output.forbidden_terms_found
    }

    for term in combined_forbidden_terms:
        if term.lower() in combined_text_lower:
            has_deterministic_violation = True
            if term.lower() not in existing_found_lower:
                auditor_output.forbidden_terms_found.append(term)
                existing_found_lower.add(term.lower())
            warning_msg = (
                f"[Trava Determinística] Termo proibido detectado no texto proposto: '{term}'."
            )
            if warning_msg not in auditor_output.warnings:
                auditor_output.warnings.append(warning_msg)

    if (
        channel == PromptChannelChoices.MERCADO_LIVRE
        and len(seo_copy.proposed_title) > ML_MAX_TITLE_LENGTH
    ):
        has_deterministic_violation = True
        len_warning = (
            f"[Trava Determinística] Título proposto possui {len(seo_copy.proposed_title)} "
            f"caracteres (máximo permitido no Mercado Livre: {ML_MAX_TITLE_LENGTH})."
        )
        if len_warning not in auditor_output.warnings:
            auditor_output.warnings.append(len_warning)

    if auditor_output.has_hallucination_risk:
        has_deterministic_violation = True
        hallucination_warning = (
            "[Trava Determinística] Risco de alucinação técnica apontado na auditoria."
        )
        if not any("alucinação" in w.lower() for w in auditor_output.warnings):
            auditor_output.warnings.append(hallucination_warning)

    if sold_quantity > 0 and channel == PromptChannelChoices.MERCADO_LIVRE:
        sold_note = (
            f"[Aviso Pre-Flight] Anúncio possui {sold_quantity} venda(s); o campo 'title' "
            "será omitido automaticamente no PATCH da Fase 3."
        )
        if sold_note not in auditor_output.warnings:
            auditor_output.warnings.append(sold_note)

    if has_deterministic_violation:
        auditor_output.confidence_score = min(
            int(auditor_output.confidence_score),
            AUDITOR_PENALTY_MAX_SCORE,
        )

    return auditor_output, usage


class OptimizationPipelineService:
    """Orquestrador sequencial dos 4 sub-agentes gerando uma OptimizationProposal auditada."""

    @classmethod
    def run_for_listing(
        cls,
        listing: Listing,
        client: GeminiStructuredClient | None = None,
    ) -> OptimizationProposal:
        """Executa o pipeline completo para um Listing e persiste a OptimizationProposal."""
        baseline_snapshot: ListingSnapshot | None = (
            listing.snapshots.filter(is_baseline=True).order_by("-captured_at").first()
            or listing.snapshots.order_by("-captured_at").first()
        )
        if baseline_snapshot is None:
            raise ValueError(
                f"O anúncio {listing.external_id} não possui ListingSnapshot baseline para otimização."
            )

        llm_client = client or GeminiStructuredClient()
        channel = listing.account.channel

        raw_item_json = baseline_snapshot.raw_item_json or {}
        raw_description_json = baseline_snapshot.raw_description_json or {}
        category_attributes_json = baseline_snapshot.category_attributes_json or []

        images = download_listing_images(raw_item_json, max_images=3)

        total_tokens = 0

        # 1. EXTRACTOR
        extracted_facts, usage_1 = run_extractor(
            raw_item_json=raw_item_json,
            raw_description_json=raw_description_json,
            channel=channel,
            client=llm_client,
            images=images,
        )
        total_tokens += usage_1["total_tokens"]

        # 2. ATTRIBUTES
        filled_attributes, usage_2 = run_attributes_agent(
            extracted_facts=extracted_facts,
            category_attributes_json=category_attributes_json,
            channel=channel,
            client=llm_client,
        )
        total_tokens += usage_2["total_tokens"]

        # 3. SEO & COPY
        seo_copy, usage_3 = run_seo_copy_agent(
            extracted_facts=extracted_facts,
            filled_attributes=filled_attributes,
            raw_item_json=raw_item_json,
            raw_description_json=raw_description_json,
            sold_quantity=listing.sold_quantity,
            channel=channel,
            client=llm_client,
        )
        total_tokens += usage_3["total_tokens"]

        # 4. AUDITOR + Trava Determinística
        auditor_result, usage_4 = run_auditor(
            extracted_facts=extracted_facts,
            filled_attributes=filled_attributes,
            seo_copy=seo_copy,
            sold_quantity=listing.sold_quantity,
            channel=channel,
            client=llm_client,
        )
        total_tokens += usage_4["total_tokens"]

        proposal = OptimizationProposal.objects.create(
            listing=listing,
            baseline_snapshot=baseline_snapshot,
            extracted_facts_json=extracted_facts.model_dump(),
            proposed_title=seo_copy.proposed_title[:255],
            proposed_description=seo_copy.proposed_description,
            proposed_attributes_json=[
                attr.model_dump() for attr in filled_attributes.attributes
            ],
            card_highlights_json=list(seo_copy.card_highlights),
            video_script_json=seo_copy.video_script.model_dump(),
            media_assets_json={},
            auditor_warnings=list(auditor_result.warnings),
            confidence_score=auditor_result.confidence_score,
            status=ProposalStatus.PENDING_REVIEW,
            llm_tokens_used=total_tokens,
        )

        listing.confidence_score = auditor_result.confidence_score
        listing.status = ListingStatus.REVIEW
        listing.save(update_fields=["confidence_score", "status", "updated_at"])

        return proposal
