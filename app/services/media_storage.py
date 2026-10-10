from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


MAX_AUDIO_BYTES = 16 * 1024 * 1024
SUPPORTED_AUDIO_MIMES = {
    "audio/aac",
    "audio/amr",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/opus",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
logger = logging.getLogger(__name__)


class MediaValidationError(ValueError):
    pass


def storage_root() -> Path:
    return Path(os.getenv("MEDIA_STORAGE_ROOT", "/var/lib/agenteos/media")).resolve()


def sanitize_original_name(value: str | None) -> str:
    name = Path(str(value or "audio")).name
    name = _SAFE_NAME.sub("_", name).strip("._")
    return (name or "audio")[:180]


def detect_audio_mime(data: bytes, declared: str | None, name: str | None) -> str:
    declared_norm = str(declared or "").split(";", 1)[0].strip().lower()
    suffix = Path(str(name or "")).suffix.lower()
    if data.startswith(b"OggS"):
        actual = "audio/ogg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        actual = "audio/wav"
    elif data.startswith(b"ID3") or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        actual = "audio/mpeg"
    elif data.startswith(b"#!AMR"):
        actual = "audio/amr"
    elif data[4:8] == b"ftyp":
        actual = "audio/mp4"
    elif declared_norm in SUPPORTED_AUDIO_MIMES:
        actual = declared_norm
    else:
        actual = mimetypes.guess_type(suffix)[0] or ""
    if actual not in SUPPORTED_AUDIO_MIMES:
        raise MediaValidationError("audio_format_not_supported")
    if declared_norm and declared_norm not in SUPPORTED_AUDIO_MIMES and declared_norm != "application/octet-stream":
        raise MediaValidationError("audio_mime_not_supported")
    return actual


def validate_audio_bytes(data: bytes, *, declared_mime: str | None, original_name: str | None) -> tuple[str, str, int]:
    if not data:
        raise MediaValidationError("audio_empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise MediaValidationError("audio_too_large")
    name = sanitize_original_name(original_name)
    return name, detect_audio_mime(data, declared_mime, name), len(data)


def probe_audio_duration_ms(path: Path) -> int | None:
    if not shutil.which("ffprobe"):
        return None
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        seconds = float(result.stdout.strip())
    except (TypeError, ValueError):
        return None
    return max(0, int(seconds * 1000))


def persist_audio_bytes(*, empresa_id: str, asset_id: str, original_name: str, data: bytes) -> tuple[str, str]:
    root = storage_root() / str(empresa_id) / "audio"
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    suffix = Path(sanitize_original_name(original_name)).suffix.lower() or ".bin"
    target = root / f"{asset_id}{suffix}"
    target.write_bytes(data)
    return str(target.relative_to(storage_root())), digest


def persist_traffic_image_bytes(*, empresa_id: str, batch_id: str, item_key: str, original_name: str, data: bytes) -> tuple[str, str]:
    """Persist campaign input outside the browser, under the tenant boundary."""
    root = storage_root() / str(empresa_id) / "traffic-meta" / str(batch_id)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.exception(
            "Traffic image persistence failed operation=mkdir destination=%s exception_class=%s errno=%s",
            root,
            type(exc).__name__,
            exc.errno,
        )
        raise
    digest = hashlib.sha256(data).hexdigest()
    suffix = Path(sanitize_original_name(original_name)).suffix.lower() or ".bin"
    safe_key = re.sub(r"[^A-Za-z0-9_-]+", "_", str(item_key or "item"))[:120] or "item"
    target = root / f"{safe_key}-{digest[:16]}{suffix}"
    if not target.exists():
        try:
            target.write_bytes(data)
        except OSError as exc:
            logger.exception(
                "Traffic image persistence failed operation=write destination=%s exception_class=%s errno=%s",
                target,
                type(exc).__name__,
                exc.errno,
            )
            raise
    return str(target.relative_to(storage_root())), digest


def resolve_storage_path(storage_key: str, *, empresa_id: str) -> Path:
    root = storage_root()
    tenant_root = (root / str(empresa_id)).resolve()
    candidate = (root / Path(str(storage_key))).resolve()
    if tenant_root not in candidate.parents:
        raise MediaValidationError("media_path_invalid")
    return candidate


def ensure_voice_variant(*, asset: Any, empresa_id: str) -> tuple[Path, str]:
    metadata = dict(getattr(asset, "metadata_json", None) or {})
    existing = str(metadata.get("voice_storage_key") or "").strip()
    if existing:
        path = resolve_storage_path(existing, empresa_id=empresa_id)
        if path.is_file():
            return path, "audio/ogg"
    source = resolve_storage_path(str(asset.storage_key), empresa_id=empresa_id)
    if not source.is_file():
        raise MediaValidationError("media_file_missing")
    if source.suffix.lower() == ".ogg" and shutil.which("ffprobe"):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,channels", "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        values = [line.strip().lower() for line in probe.stdout.splitlines() if line.strip()]
        if len(values) >= 2 and values[0] == "opus" and values[1] == "1":
            return source, "audio/ogg"
    if not shutil.which("ffmpeg"):
        raise MediaValidationError("audio_conversion_unavailable")
    target = source.with_name(f"{source.stem}.voice.ogg")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(source), "-ac", "1", "-c:a", "libopus", str(target)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    return target, "audio/ogg"
