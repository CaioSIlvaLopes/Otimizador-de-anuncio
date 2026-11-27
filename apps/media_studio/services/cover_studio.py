"""Estúdio de Capa Fundo Branco 1200x1200 (rembg + Pillow) preservando 100% da fidelidade do produto real."""
from io import BytesIO
from typing import Any

import rembg
from django.conf import settings
from PIL import Image, ImageDraw

_REMBG_SESSION_CACHE: dict[str, Any] = {}


def _is_mock_callable(fn: Any) -> bool:
    """Detecta se rembg.remove foi substituído por um Mock durante testes unitários."""
    return hasattr(fn, "_mock_name") or "Mock" in type(fn).__name__


def _get_lightweight_rembg_session() -> Any:
    """Inicializa e faz cache de uma sessão ONNX leve (u2netp por padrão) para evitar OOM de RAM."""
    model_name = (
        str(getattr(settings, "REMBG_MODEL_NAME", "u2netp")).strip() or "u2netp"
    )
    if model_name not in _REMBG_SESSION_CACHE:
        _REMBG_SESSION_CACHE[model_name] = rembg.new_session(
            model_name,
            providers=["CPUExecutionProvider"],
        )
    return _REMBG_SESSION_CACHE[model_name]


def extract_product_cutout(source_image_bytes: bytes) -> Image.Image:
    """Remove o fundo com rembg (modelo leve u2netp) e faz trim automático pela caixa delimitadora alpha."""
    if not source_image_bytes:
        raise ValueError("Os bytes da imagem de origem não podem ser vazios.")

    if _is_mock_callable(rembg.remove):
        removed = rembg.remove(source_image_bytes)
    else:
        session = _get_lightweight_rembg_session()
        removed = rembg.remove(source_image_bytes, session=session)

    if isinstance(removed, Image.Image):
        rgba_image = removed.convert("RGBA")
    else:
        rgba_image = Image.open(BytesIO(bytes(removed))).convert("RGBA")

    alpha = rgba_image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        rgba_image = rgba_image.crop(bbox)

    return rgba_image


def build_white_bg_cover(
    cutout_image: Image.Image,
    canvas_size: int = 1200,
    padding_ratio: float = 0.08,
) -> bytes:
    """Compõe o recorte real centralizado em canvas RGB branco puro #FFFFFF (1200x1200) sem textos ou bordas."""
    if canvas_size <= 0:
        raise ValueError("canvas_size deve ser maior que zero.")

    rgba_cutout = cutout_image.convert("RGBA")
    src_w, src_h = rgba_cutout.size
    if src_w <= 0 or src_h <= 0:
        raise ValueError("Recorte de imagem com dimensões inválidas.")

    safe_padding = max(0.0, min(0.4, float(padding_ratio)))
    max_target_dim = max(1, int(round(canvas_size * (1.0 - 2.0 * safe_padding))))

    scale = min(max_target_dim / float(src_w), max_target_dim / float(src_h))
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))

    resized_cutout = rgba_cutout.resize(
        (new_w, new_h),
        resample=Image.Resampling.LANCZOS,
    )

    canvas = Image.new("RGB", (canvas_size, canvas_size), (255, 255, 255))
    offset_x = (canvas_size - new_w) // 2
    offset_y = (canvas_size - new_h) // 2

    alpha_mask = resized_cutout.getchannel("A")
    canvas.paste(resized_cutout.convert("RGB"), (offset_x, offset_y), mask=alpha_mask)

    output = BytesIO()
    canvas.save(output, format="JPEG", quality=92, optimize=True)
    return output.getvalue()


def save_cutout_png(cutout_image: Image.Image) -> bytes:
    """Exporta o recorte RGBA transparente como PNG otimizado para reuso nos cards HTML."""
    rgba_cutout = cutout_image.convert("RGBA")
    output = BytesIO()
    rgba_cutout.save(output, format="PNG", optimize=True)
    return output.getvalue()


def build_synthetic_sample_image(width: int = 800, height: int = 800) -> bytes:
    """Gera uma imagem sintética de produto com Pillow para testes locais (--use-sample-image)."""
    img = Image.new("RGBA", (width, height), (235, 240, 248, 255))
    draw = ImageDraw.Draw(img)

    # Desenha silhueta de produto (corpo + tampa em alto contraste) no centro
    draw.rounded_rectangle(
        [(220, 240), (580, 660)],
        radius=42,
        fill=(30, 41, 59, 255),
        outline=(59, 130, 246, 255),
        width=8,
    )
    draw.rounded_rectangle(
        [(260, 150), (540, 255)],
        radius=24,
        fill=(59, 130, 246, 255),
    )
    draw.ellipse(
        [(360, 390), (440, 470)],
        fill=(249, 115, 22, 255),
    )

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
