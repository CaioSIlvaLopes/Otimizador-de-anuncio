"""Views do Painel Web de Revisão Antes vs Depois (Dark Mode)."""
import json
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.accounts_vault.models import ChannelChoices, MarketplaceAccount
from apps.catalog.models import OptimizationProposal, ProposalStatus
from apps.catalog.services.publisher import publish_proposal, simulate_proposal_patch
from apps.catalog.services.rollback import rollback_listing_to_baseline
from apps.governance.models import DailyQuotaLog
from apps.marketplaces.exceptions import (
    CircuitBreakerOpenError,
    DailyPatchQuotaExceededError,
    MarketplaceAPIError,
    WriteDisabledError,
)
from apps.marketplaces.sanitizer import FORBIDDEN_ATTRIBUTE_IDS


def _extract_edited_attributes_from_post(
    request: HttpRequest,
    fallback_attributes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reconstrói a lista de atributos editados no formulário garantindo exclusão de SELLER_SKU."""
    attr_ids = request.POST.getlist("attr_id")
    attr_names = request.POST.getlist("attr_name")
    attr_values = request.POST.getlist("attr_value")
    attr_evidences = request.POST.getlist("attr_evidence")

    if not attr_ids:
        return fallback_attributes

    updated: list[dict[str, Any]] = []
    for idx, raw_id in enumerate(attr_ids):
        attr_id = str(raw_id or "").strip()
        if not attr_id or attr_id.upper() in FORBIDDEN_ATTRIBUTE_IDS:
            continue
        name = (
            str(attr_names[idx]).strip()
            if idx < len(attr_names)
            else attr_id
        )
        value_name = (
            str(attr_values[idx]).strip()
            if idx < len(attr_values)
            else ""
        )
        evidence = (
            str(attr_evidences[idx]).strip()
            if idx < len(attr_evidences)
            else "Editado manualmente pelo revisor"
        )
        if not value_name:
            continue
        updated.append(
            {
                "id": attr_id,
                "name": name,
                "value_name": value_name,
                "source_evidence": evidence,
            }
        )
    return updated


def _apply_edits_from_post(
    request: HttpRequest,
    proposal: OptimizationProposal,
) -> None:
    """Atualiza proposed_title, proposed_description e proposed_attributes_json se presentes no POST."""
    if "proposed_title" in request.POST:
        proposal.proposed_title = request.POST.get("proposed_title", "").strip()[:255]
    if "proposed_description" in request.POST:
        proposal.proposed_description = request.POST.get(
            "proposed_description", ""
        ).strip()
    if "attr_id" in request.POST:
        proposal.proposed_attributes_json = _extract_edited_attributes_from_post(
            request,
            proposal.proposed_attributes_json or [],
        )


@staff_member_required
@require_GET
def dashboard_queue(request: HttpRequest) -> HttpResponse:
    """Tela 1: Fila de Revisão com KPIs operacionais, filtros e listagem de propostas."""
    proposals_qs = OptimizationProposal.objects.select_related(
        "listing",
        "listing__account",
    ).order_by("-created_at")

    selected_account = request.GET.get("account", "").strip()
    selected_channel = request.GET.get("channel", "").strip()
    selected_status = request.GET.get("status", "").strip()

    if selected_account.isdigit():
        proposals_qs = proposals_qs.filter(listing__account_id=int(selected_account))
    if selected_channel:
        proposals_qs = proposals_qs.filter(listing__account__channel=selected_channel)
    if selected_status:
        proposals_qs = proposals_qs.filter(status=selected_status)

    quota_today = DailyQuotaLog.get_today()
    daily_token_limit = int(getattr(settings, "DAILY_LLM_TOKEN_LIMIT", 500000))

    kpi = {
        "pending_review_count": OptimizationProposal.objects.filter(
            status=ProposalStatus.PENDING_REVIEW
        ).count(),
        "approved_count": OptimizationProposal.objects.filter(
            status=ProposalStatus.APPROVED
        ).count(),
        "published_count": OptimizationProposal.objects.filter(
            status=ProposalStatus.PUBLISHED
        ).count(),
        "tokens_today": quota_today.total_llm_tokens,
        "daily_token_limit": daily_token_limit,
        "circuit_breaker_accounts_count": MarketplaceAccount.objects.filter(
            is_paused_by_circuit_breaker=True
        ).count(),
    }

    context = {
        "marketplace_write_enabled": bool(
            getattr(settings, "MARKETPLACE_WRITE_ENABLED", False)
        ),
        "kpi": kpi,
        "proposals": proposals_qs[:100],
        "accounts": MarketplaceAccount.objects.all().order_by("name"),
        "channel_choices": ChannelChoices.choices,
        "status_choices": ProposalStatus.choices,
        "selected_account": selected_account,
        "selected_channel": selected_channel,
        "selected_status": selected_status,
    }
    return render(request, "dashboard/queue.html", context)


@staff_member_required
@require_http_methods(["GET", "POST"])
def proposal_detail(request: HttpRequest, proposal_id: int) -> HttpResponse:
    """Tela 2: Revisão Lado a Lado Antes vs Depois, Dry-Run, Publicação e Rollback."""
    proposal = get_object_or_404(
        OptimizationProposal.objects.select_related(
            "listing",
            "listing__account",
            "baseline_snapshot",
        ),
        pk=proposal_id,
    )
    listing = proposal.listing
    baseline = proposal.baseline_snapshot
    write_enabled = bool(getattr(settings, "MARKETPLACE_WRITE_ENABLED", False))

    dry_run_result: dict[str, Any] | None = None
    dry_run_json_pretty: str = ""

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        if action == "save_approve":
            _apply_edits_from_post(request, proposal)
            proposal.status = ProposalStatus.APPROVED
            proposal.save(
                update_fields=[
                    "proposed_title",
                    "proposed_description",
                    "proposed_attributes_json",
                    "status",
                    "updated_at",
                ]
            )
            messages.success(
                request,
                f"Proposta #{proposal.pk} salva e marcada como APROVADA.",
            )
            return redirect("dashboard:proposal_detail", proposal_id=proposal.pk)

        elif action == "reject":
            proposal.status = ProposalStatus.REJECTED
            proposal.save(update_fields=["status", "updated_at"])
            messages.warning(
                request,
                f"Proposta #{proposal.pk} marcada como REJEITADA.",
            )
            return redirect("dashboard:proposal_detail", proposal_id=proposal.pk)

        elif action == "dry_run":
            _apply_edits_from_post(request, proposal)
            proposal.save(
                update_fields=[
                    "proposed_title",
                    "proposed_description",
                    "proposed_attributes_json",
                    "updated_at",
                ]
            )
            dry_run_result = simulate_proposal_patch(proposal)
            dry_run_json_pretty = json.dumps(
                dry_run_result,
                indent=2,
                ensure_ascii=False,
            )
            messages.info(
                request,
                "Simulação Dry-Run executada com sucesso (nenhum dado foi enviado ao marketplace).",
            )

        elif action == "publish":
            _apply_edits_from_post(request, proposal)
            proposal.save(
                update_fields=[
                    "proposed_title",
                    "proposed_description",
                    "proposed_attributes_json",
                    "updated_at",
                ]
            )
            try:
                pub_result = publish_proposal(proposal)
                dry_run_result = pub_result
                dry_run_json_pretty = json.dumps(
                    pub_result,
                    indent=2,
                    ensure_ascii=False,
                )
                messages.success(
                    request,
                    f"Anúncio {listing.external_id} atualizado com sucesso no marketplace!",
                )
            except (
                WriteDisabledError,
                CircuitBreakerOpenError,
                DailyPatchQuotaExceededError,
                MarketplaceAPIError,
            ) as exc:
                messages.error(request, f"Não foi possível publicar: {exc}")

        elif action == "rollback":
            is_dry_rollback = not write_enabled
            try:
                rb_result = rollback_listing_to_baseline(
                    listing=listing,
                    dry_run=is_dry_rollback,
                )
                dry_run_result = rb_result
                dry_run_json_pretty = json.dumps(
                    rb_result,
                    indent=2,
                    ensure_ascii=False,
                )
                if is_dry_rollback:
                    messages.warning(
                        request,
                        "Modo Somente Leitura ativo: exibindo preview Dry-Run do payload de Rollback.",
                    )
                else:
                    messages.success(
                        request,
                        f"Rollback do anúncio {listing.external_id} para o Snapshot Baseline concluído!",
                    )
            except (
                WriteDisabledError,
                CircuitBreakerOpenError,
                MarketplaceAPIError,
                ValueError,
            ) as exc:
                messages.error(request, f"Falha no Rollback: {exc}")

    raw_item = baseline.raw_item_json or {}
    raw_desc = baseline.raw_description_json or {}

    original_pictures: list[str] = []
    for pic in raw_item.get("pictures") or []:
        if isinstance(pic, dict):
            url = pic.get("secure_url") or pic.get("url")
            if url:
                original_pictures.append(str(url))

    original_attributes = [
        {
            "id": a.get("id", ""),
            "name": a.get("name", a.get("id", "")),
            "value_name": a.get("value_name", ""),
        }
        for a in (raw_item.get("attributes") or [])
        if isinstance(a, dict)
    ]

    context = {
        "marketplace_write_enabled": write_enabled,
        "proposal": proposal,
        "listing": listing,
        "baseline": baseline,
        "original_title": raw_item.get("title") or listing.original_title,
        "original_description": raw_desc.get("plain_text", ""),
        "original_attributes": original_attributes,
        "original_pictures": original_pictures,
        "proposed_attributes": proposal.proposed_attributes_json or [],
        "card_highlights": proposal.card_highlights_json or [],
        "video_script": proposal.video_script_json or {},
        "extracted_facts": proposal.extracted_facts_json or {},
        "dry_run_result": dry_run_result,
        "dry_run_json_pretty": dry_run_json_pretty,
    }
    return render(request, "dashboard/proposal_detail.html", context)
