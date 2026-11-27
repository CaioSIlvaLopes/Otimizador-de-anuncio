"""Schemas Pydantic v2 estritos para Structured Outputs dos 4 sub-agentes Gemini 2.5 Flash."""
from typing import Optional

from pydantic import BaseModel, Field


class ExtractedFacts(BaseModel):
    """Fatos técnicos comprovados extraídos do anúncio bruto e fotos (Anti-Alucinação)."""

    product_type: str
    brand: Optional[str] = None
    model: Optional[str] = None
    material: Optional[str] = None
    color: Optional[str] = None
    voltage: Optional[str] = None
    dimensions: Optional[str] = None
    weight: Optional[str] = None
    compatibility: list[str] = Field(default_factory=list)
    included_items: list[str] = Field(default_factory=list)
    verified_features: list[str] = Field(default_factory=list)
    missing_critical_info: list[str] = Field(default_factory=list)


class FilledAttribute(BaseModel):
    """Atributo da categoria preenchido com evidência comprovada na fonte."""

    id: str
    name: str
    value_name: str
    source_evidence: str


class AttributesOutput(BaseModel):
    """Saída estruturada do sub-agente de Ficha Técnica."""

    attributes: list[FilledAttribute] = Field(default_factory=list)
    skipped_required_ids: list[str] = Field(default_factory=list)


class VideoScriptDraft(BaseModel):
    """Rascunho de roteiro curto para vídeo promocional vertical/quadrado (Fase 4)."""

    hook: str
    scenes: list[str] = Field(default_factory=list)
    cta: str


class SeoCopyOutput(BaseModel):
    """Saída estruturada do sub-agente de SEO & Copywriting."""

    proposed_title: str
    proposed_description: str
    card_highlights: list[str] = Field(default_factory=list)
    video_script: VideoScriptDraft


class AuditorOutput(BaseModel):
    """Saída estruturada do sub-agente Auditor de Qualidade e Anti-Alucinação."""

    confidence_score: int = Field(ge=0, le=100)
    has_hallucination_risk: bool
    forbidden_terms_found: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str
