"""Validation shared by the local image analysis and Meta upload paths."""

from __future__ import annotations

from io import BytesIO


class ImageValidationError(ValueError):
    pass


def validate_image_bytes(content: bytes, mime_type: str, *, max_bytes: int = 10 * 1024 * 1024) -> tuple[int, int, str]:
    raw_mime = str(mime_type or "").strip().lower()
    if raw_mime not in {"image/jpeg", "image/png"}:
        raise ImageValidationError("IMAGE_FORMAT_UNSUPPORTED")
    if not content or len(content) > max_bytes:
        raise ImageValidationError("IMAGE_SIZE_INVALID")
    try:
        from PIL import Image

        with Image.open(BytesIO(content)) as image:
            image.verify()
        with Image.open(BytesIO(content)) as image:
            detected = str(image.format or "").upper()
            expected = "JPEG" if raw_mime == "image/jpeg" else "PNG"
            if detected != expected:
                raise ImageValidationError("IMAGE_MAGIC_BYTES_MISMATCH")
            width, height = image.size
    except ImageValidationError:
        raise
    except Exception as exc:
        raise ImageValidationError("IMAGE_DECODE_FAILED") from exc
    if width < 10 or height < 10:
        raise ImageValidationError("IMAGE_DIMENSIONS_INVALID")
    return width, height, detected
