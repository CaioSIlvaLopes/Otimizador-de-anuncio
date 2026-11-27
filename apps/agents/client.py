"""Cliente Gemini 2.5 Flash com Structured Outputs, controle de cota diária e download de imagens."""
from typing import Any, TypeVar

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from google import genai
from google.genai import types
from pydantic import BaseModel

from apps.governance.models import DailyQuotaLog

T = TypeVar("T", bound=BaseModel)

IMAGE_DOWNLOAD_TIMEOUT = 10.0


class DailyQuotaExceededError(RuntimeError):
    """Lançada quando o consumo diário de tokens atinge ou ultrapassa DAILY_LLM_TOKEN_LIMIT."""


def download_listing_images(
    raw_item_json: dict[str, Any],
    max_images: int = 3,
) -> list[tuple[bytes, str]]:
    """Baixa até max_images fotos do anúncio com timeout de 10s, ignorando falhas sem travar o pipeline."""
    if not isinstance(raw_item_json, dict) or max_images <= 0:
        return []

    urls: list[str] = []
    pictures = raw_item_json.get("pictures")
    if isinstance(pictures, list):
        for pic in pictures:
            if isinstance(pic, dict):
                url = pic.get("secure_url") or pic.get("url")
                if url and isinstance(url, str) and url.startswith("http"):
                    urls.append(url)
            if len(urls) >= max_images:
                break

    if not urls:
        thumb = raw_item_json.get("secure_thumbnail") or raw_item_json.get("thumbnail")
        if thumb and isinstance(thumb, str) and thumb.startswith("http"):
            urls.append(thumb)

    downloaded: list[tuple[bytes, str]] = []
    if not urls:
        return downloaded

    try:
        with httpx.Client(timeout=IMAGE_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            for url in urls[:max_images]:
                try:
                    resp = client.get(url)
                    if resp.status_code == 200 and resp.content:
                        content_type = (
                            resp.headers.get("content-type", "image/jpeg")
                            .split(";")[0]
                            .strip()
                        )
                        if not content_type.startswith("image/"):
                            content_type = "image/jpeg"
                        downloaded.append((resp.content, content_type))
                except (httpx.RequestError, Exception):
                    continue
    except Exception:
        return downloaded

    return downloaded


class GeminiStructuredClient:
    """Wrapper do Google GenAI SDK com validação de orçamento diário e saída Pydantic v2."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
    ) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else str(getattr(settings, "GEMINI_API_KEY", "")).strip()
        )
        self.model_name = (
            model_name
            if model_name is not None
            else str(getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")).strip()
        )
        self._client: genai.Client | None = None

    def _get_genai_client(self) -> genai.Client:
        if not self.api_key:
            raise ImproperlyConfigured(
                "GEMINI_API_KEY não está configurada. Defina sua chave no arquivo .env."
            )
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[T],
        images: list[tuple[bytes, str]] | None = None,
        temperature: float = 0.2,
    ) -> tuple[T, dict[str, int]]:
        """Executa uma chamada estruturada ao Gemini contabilizando tokens no DailyQuotaLog."""
        quota_log = DailyQuotaLog.get_today()
        if not quota_log.has_llm_budget():
            limit = int(getattr(settings, "DAILY_LLM_TOKEN_LIMIT", 500000))
            raise DailyQuotaExceededError(
                f"Orçamento diário de tokens LLM esgotado ({quota_log.total_llm_tokens}/{limit})."
            )

        client = self._get_genai_client()

        contents: list[Any] = []
        if images:
            for img_bytes, mime_type in images:
                contents.append(
                    types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
                )
        contents.append(user_prompt)

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=temperature,
        )

        response = client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )

        usage_meta = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage_meta, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage_meta, "candidates_token_count", 0) or 0)
        quota_log.record_llm_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, response_schema):
            parsed_obj = parsed
        elif isinstance(parsed, dict):
            parsed_obj = response_schema.model_validate(parsed)
        else:
            raw_text = getattr(response, "text", "") or "{}"
            parsed_obj = response_schema.model_validate_json(raw_text)

        usage_dict = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        return parsed_obj, usage_dict
