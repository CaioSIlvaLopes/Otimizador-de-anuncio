"""Tasks Celery do estúdio local de mídia (isoladas estritamente na fila queue_media)."""
from typing import Any

from celery import shared_task

from apps.catalog.models import OptimizationProposal
from apps.media_studio.services.pipeline import generate_images_for_proposal


@shared_task(
    name="apps.media_studio.tasks.generate_proposal_images_task",
    queue="queue_media",
)
def generate_proposal_images_task(
    proposal_id: int,
    source_image_path: str | None = None,
    use_sample_image: bool = False,
) -> dict[str, Any]:
    """Executa a geração local de capa 1200x1200 e cards informativos na fila queue_media."""
    try:
        proposal = OptimizationProposal.objects.select_related(
            "listing",
            "baseline_snapshot",
        ).get(pk=proposal_id)
    except OptimizationProposal.DoesNotExist:
        return {
            "status": "error",
            "proposal_id": proposal_id,
            "error": f"OptimizationProposal ID={proposal_id} não encontrada.",
        }

    try:
        assets = generate_images_for_proposal(
            proposal=proposal,
            source_image_path=source_image_path,
            use_sample_image=use_sample_image,
        )
        return {
            "status": "ok",
            "proposal_id": proposal.pk,
            "assets_generated": [
                {
                    "id": a.pk,
                    "asset_type": a.asset_type,
                    "file": a.file.name if a.file else "",
                    "width": a.width,
                    "height": a.height,
                }
                for a in assets
            ],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "proposal_id": proposal.pk,
            "error": str(exc),
        }
