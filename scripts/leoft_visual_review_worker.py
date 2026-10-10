#!/usr/bin/env python3
"""Resumable real-vision worker for REAL_VISUAL_REVIEW_20260929.

Vision is performed by the authenticated Codex CLI with a local image attachment.
Catalog text is queried only after a valid, durable visual interpretation exists.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_ID = "REAL_VISUAL_REVIEW_20260929"
ROOT = Path("/srv/leoft-media/analysis/real-visual-review-20260929")
MEDIA_ROOT = Path("/srv/leoft-media")
CATALOG_ROOT = Path("/srv/leoft-media/catalog")
PROGRESS = ROOT / "progress.json"
LOG = ROOT / "worker.log"
LOCK = ROOT / "worker.lock"
REVIEW_CSV = CATALOG_ROOT / "real_visual_review_20260929.csv"
CHANGES_CSV = CATALOG_ROOT / "real_visual_changes_20260929.csv"
REQUIRED_CSV = CATALOG_ROOT / "real_visual_review_required_20260929.csv"
RECOVERY_MANIFEST = CATALOG_ROOT / "catalog_recovery_20260928.csv"
FINAL_REPORT = Path("/root/agenteos/reports/LEOFT_REAL_VISUAL_CATALOG_REVIEW_20260929.md")
PANEL_BASE_URL = os.getenv("LEOFT_PANEL_BASE_URL", "http://127.0.0.1:8091")
BACKEND_BASE_URL = os.getenv("LEOFT_BACKEND_BASE_URL", "http://127.0.0.1:8000")
DB_CONTAINER = os.getenv("LEOFT_DB_CONTAINER", "agent_os_db")
DB_NAME = os.getenv("LEOFT_DB_NAME", "agent_os")
DB_USER = os.getenv("LEOFT_DB_USER", "agenteos_admin")
VISUAL_METHOD = "CODEX_CLI_IMAGE_ATTACHMENT"
CODEX_PATH = Path(shutil.which("codex") or "/root/.local/bin/codex").resolve()
CODEX_TIMEOUT_SECONDS = 900
CODEX_ATTEMPTS = 3
CODEX_RETRY_DELAY_SECONDS = 20
IMAGE_FIELDS = {
    "visual_observation", "visual_interpretation", "exercise_name",
    "primary_muscle_group", "secondary_muscle_groups", "equipment",
    "movement_pattern", "body_position", "laterality", "grip", "limb_path",
    "angle_or_orientation", "machine_or_free_weight", "notable_variation",
    "name_confidence", "muscle_confidence", "equipment_confidence",
    "variation_confidence", "description", "instructions",
}
CONFIDENCE = {"HIGH", "MEDIUM", "LOW", "NONE"}
GENERIC = re.compile(
    r"^(exerc[ií]cio(?:s)?(?: de)? muscula[cç][aã]o|exerc[ií]cio(?:s)?(?: de)? treino|"
    r"exerc[ií]cio(?:s)? com equipamento|pessoa(?:s)? realizando(?: um)?(?: exerc[ií]cio| movimento)?|"
    r"movimento(?: de treino)?|treino(?: de muscula[cç][aã]o)?|n[aã]o [ée] poss[ií]vel determinar|n[aã]o identificado|unknown|"
    r"exercise|workout|unspecified|)$", re.I,
)
REVIEW_FIELDS = [
    "catalog_entry_id", "catalog_media_id", "sha256", "visual_observation",
    "visual_interpretation", "visual_method", "old_name", "new_name",
    "name_confidence", "old_muscle", "new_muscle", "muscle_confidence",
    "old_equipment", "new_equipment", "equipment_confidence", "variation",
    "variation_confidence", "secondary_muscles", "movement_pattern",
    "body_position", "laterality", "grip", "limb_path", "angle_or_orientation",
    "notable_variation", "comparison", "database_changed", "persist_status",
    "timestamp", "error",
]
CHANGES_FIELDS = [
    "catalog_entry_id", "catalog_media_id", "sha256", "visual_observation",
    "visual_interpretation", "visual_method", "old_name", "new_name",
    "name_confidence", "old_muscle", "new_muscle", "muscle_confidence",
    "old_equipment", "new_equipment", "equipment_confidence", "comparison", "timestamp",
]
REQUIRED_FIELDS = [
    "catalog_entry_id", "catalog_media_id", "sha256", "visual_observation",
    "visual_interpretation", "visual_method", "name_confidence", "muscle_confidence",
    "equipment_confidence", "variation_confidence", "comparison", "persist_status", "timestamp",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    line = f"[{now()}] {message}"
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(line, flush=True)


def atomic_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def psql(sql: str, *, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", DB_CONTAINER, "psql", "-X", "-q", "-t", "-A",
             "-v", "ON_ERROR_STOP=1", "-U", DB_USER, "-d", DB_NAME],
            input=sql, text=True, capture_output=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkerStop(f"database access unavailable: {type(exc).__name__}") from exc
    if check and result.returncode:
        raise WorkerStop(f"database command failed ({result.returncode}); details omitted")
    return result.stdout.strip()


def sqlq(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def json_sql(value: Any) -> str:
    return sqlq(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + "::jsonb"


def load_progress() -> dict[str, Any]:
    state = json.loads(PROGRESS.read_text(encoding="utf-8"))
    if state.get("run_id") != RUN_ID or int(state.get("total", 0)) != 1074:
        raise RuntimeError("checkpoint run/total mismatch; refusing to continue")
    for key in ("pending_ids", "frames_ready_ids", "visually_inspected_ids", "persisted_ids", "failed_ids"):
        state.setdefault(key, [])
    state.setdefault("status_by_id", {})
    return state


def save_progress(state: dict[str, Any]) -> None:
    state["updated_at"] = now()
    atomic_json(PROGRESS, state)


def csv_append(path: Path, fields: list[str], row: dict[str, Any]) -> None:
    # Keep exactly one current row per entry. A legacy non-Codex row can be
    # replaced only after a new real Codex interpretation has been persisted.
    exists = path.exists() and path.stat().st_size > 0
    if exists:
        with path.open(newline="", encoding="utf-8") as stream:
            header = next(csv.reader(stream), [])
        if header != fields:
            raise RuntimeError(f"CSV header mismatch at {path}; preserving file and stopping")
    with path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)
        stream.flush()
        os.fsync(stream.fileno())


def csv_upsert(path: Path, fields: list[str], row: dict[str, Any]) -> None:
    rows = read_csv(path)
    if rows and list(rows[0]) != fields:
        raise RuntimeError(f"CSV header mismatch at {path}; preserving file and stopping")
    entry_id = row.get("catalog_entry_id")
    matches = [index for index, old in enumerate(rows) if old.get("catalog_entry_id") == entry_id]
    if matches:
        first = matches[0]
        for index in reversed(matches):
            rows.pop(index)
        rows.insert(first, {field: row.get(field, "") for field in fields})
    else:
        rows.append({field: row.get(field, "") for field in fields})
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def optional_positive_number(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", text):
        return None
    number = float(text)
    return number if math.isfinite(number) and number > 0 else None


def source_media_kind(source: Path, media: dict[str, Any]) -> str:
    with source.open("rb") as stream:
        signature = stream.read(16)
    if signature.startswith((b"GIF87a", b"GIF89a")):
        return "animated"
    if (len(signature) >= 12 and signature[4:8] == b"ftyp") or signature.startswith(b"\x1a\x45\xdf\xa3"):
        return "animated"
    mime = str(media.get("mime_type") or "").lower()
    if mime.startswith("video/"):
        return "animated"
    return "static"


def _extract_frame(source: Path, output: Path, second: float, filter_chain: str) -> bool:
    """Try timestamp seeking both before and after input; isolate bad samples."""
    commands = [
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{second:.3f}", "-i", str(source),
         "-frames:v", "1", "-vf", filter_chain, str(output)],
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(source), "-ss", f"{second:.3f}",
         "-frames:v", "1", "-vf", filter_chain, str(output)],
    ]
    for attempt, command in enumerate(commands, 1):
        output.unlink(missing_ok=True)
        try:
            result = subprocess.run(command, check=False, timeout=45, capture_output=True)
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0 and output.is_file() and output.stat().st_size:
            return True
        if attempt < len(commands):
            time.sleep(0.5)
    output.unlink(missing_ok=True)
    return False


def load_media(entry_id: str) -> dict[str, Any]:
    query = """SELECT row_to_json(x)::text FROM (
      SELECT e.id::text AS entry_id,m.id::text AS media_id,m.sha256,
             m.storage_path,m.format,m.mime_type
      FROM public.leoft_catalog_entries e
      JOIN public.leoft_catalog_media m ON m.catalog_entry_id=e.id
      WHERE e.id=%s
    ) x""" % sqlq(entry_id)
    raw = psql(query)
    if not raw:
        raise ItemFailure("catalog media relation missing")
    row = json.loads(raw)
    if row["entry_id"] != entry_id:
        raise RuntimeError("catalog entry mismatch")
    return row


def prepare_image(entry_id: str, media: dict[str, Any], *, source: Path | None = None, work_dir: Path | None = None) -> tuple[Path, Path]:
    source = source or resolve_and_validate_media(media)
    work = work_dir or (ROOT / "worker_tmp" / uuid.uuid4().hex)
    if work.exists():
        shutil.rmtree(work)
    if source_media_kind(source, media) == "animated":
        work.mkdir(parents=True, exist_ok=True)
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=duration", "-of", "json", str(source)],
                text=True, capture_output=True, check=True, timeout=45,
            )
            probe_data = json.loads(probe.stdout or "{}")
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
            # Broken/incomplete probe metadata is recoverable: decode a bounded
            # contact sheet directly and fall back to timestamp samples below.
            probe_data = {}
            log(f"entry={entry_id} ffprobe=INCOMPLETE fallback=direct_decode")
        duration_values = [optional_positive_number((probe_data.get("format") or {}).get("duration"))]
        duration_values.extend(optional_positive_number(stream.get("duration")) for stream in probe_data.get("streams", []))
        duration = max((value for value in duration_values if value is not None), default=None)
        frames = 8 if str(media.get("format") or "").lower() == "gif" else 10
        font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if duration is None:
            # Duration is optional metadata. For animated media with no usable
            # duration, sample decoded frames directly instead of coercing N/A.
            # ffmpeg can return non-zero after producing useful partial frames;
            # retain those, then use per-timestamp fallback if none survived.
            try:
                subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(source),
                 "-vf", f"fps=1,scale=480:360:force_original_aspect_ratio=decrease,pad=480:360:(ow-iw)/2:(oh-ih)/2:white,drawtext=fontfile={font}:text='frame %{n}':x=8:y=8:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.65",
                 "-frames:v", str(frames), str(work / "decoded_%02d.jpg")],
                    check=False, timeout=90, capture_output=True,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            decoded = sorted(path for path in work.glob("decoded_*.jpg") if path.is_file() and path.stat().st_size)
            if not decoded:
                for idx in range(frames):
                    output = work / f"decoded_{idx + 1:02d}.jpg"
                    vf = ("scale=480:360:force_original_aspect_ratio=decrease,"
                          "pad=480:360:(ow-iw)/2:(oh-ih)/2:white,"
                          f"drawtext=fontfile={font}:text='sample {idx + 1}':x=8:y=8:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.65")
                    _extract_frame(source, output, float(idx), vf)
        else:
            for idx in range(frames):
                # Sample evenly inside the playable duration so the final seek
                # cannot land on an exclusive EOF timestamp.
                sec = duration * idx / frames
                output = work / f"frame_{idx + 1:02d}.jpg"
                vf = ("scale=480:360:force_original_aspect_ratio=decrease,"
                      "pad=480:360:(ow-iw)/2:(oh-ih)/2:white,"
                      f"drawtext=fontfile={font}:text='t={sec:.2f}s':x=8:y=8:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.65")
                if not _extract_frame(source, output, sec, vf):
                    log(f"entry={entry_id} frame_sample={idx + 1}/{frames} result=SKIPPED")
        frame_paths = sorted(path for path in work.glob("decoded_*.jpg") if path.is_file() and path.stat().st_size)
        if not frame_paths:
            frame_paths = sorted(path for path in work.glob("frame_*.jpg") if path.is_file() and path.stat().st_size)
        if not frame_paths:
            raise ItemFailure("animated media produced no decodable frames")
        # Missing samples are allowed, but ffmpeg's numbered input pattern is
        # contiguous. Normalize valid frames before building the contact sheet.
        normalized = []
        for index, old_path in enumerate(frame_paths, 1):
            new_path = work / f"frame_{index:02d}.jpg"
            if old_path != new_path:
                old_path.replace(new_path)
            normalized.append(new_path)
        frame_paths = normalized
        sheet = work / "contact_sheet.jpg"
        cols = min(4 if frames == 8 else 5, len(frame_paths))
        rows = max(1, math.ceil(len(frame_paths) / cols))
        input_pattern = str(work / "frame_%02d.jpg")
        try:
            subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-v", "error", "-framerate", "1", "-i",
             input_pattern, "-vf", f"tile={cols}x{rows}:padding=8:margin=8:color=white",
             "-frames:v", "1", "-q:v", "3", str(sheet)],
                check=True, timeout=60, capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ItemFailure(f"contact sheet generation failed: {type(exc).__name__}") from exc
        if not sheet.is_file() or sheet.stat().st_size == 0:
            raise ItemFailure("contact sheet was not created")
    else:
        # Preserve the exact original bytes, while attaching only a neutral
        # filename from an opaque temporary directory to avoid semantic leakage.
        work.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as stream:
            signature = stream.read(16)
        if signature.startswith(b"\xff\xd8\xff"):
            suffix = ".jpg"
        elif signature.startswith(b"\x89PNG\r\n\x1a\n"):
            suffix = ".png"
        elif signature.startswith((b"GIF87a", b"GIF89a")):
            suffix = ".gif"
        elif signature.startswith(b"RIFF") and signature[8:12] == b"WEBP":
            suffix = ".webp"
        else:
            suffix = source.suffix.lower() or ".img"
        staged = work / f"input_image{suffix}"
        shutil.copyfile(source, staged)
        if hashlib.sha256(staged.read_bytes()).hexdigest().lower() != str(media["sha256"]).lower():
            raise ItemFailure("staged static image SHA-256 mismatch")
        return source, staged
    return source, sheet


class CodexStop(RuntimeError):
    """A global condition that must stop the worker without failing an item."""


class CodexRetryable(RuntimeError):
    """A transient Codex invocation failure eligible for bounded retry."""


class ReviewRequired(RuntimeError):
    """Vision output stayed inconclusive after the one focused second attempt."""

    def __init__(self, message: str, result: dict[str, Any] | None = None, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.result = result
        self.evidence = evidence or {}


class WorkerStop(RuntimeError):
    """A global dependency/runtime condition that must not fail the current item."""


class ItemFailure(RuntimeError):
    """A per-item media/integrity defect that cannot be retried safely."""


def codex_env() -> dict[str, str]:
    env = os.environ.copy()
    # Prove operation through the existing ChatGPT login, never the API-key path.
    env.pop("OPENAI_API_KEY", None)
    env["HOME"] = "/root"
    env["CODEX_HOME"] = "/root/.codex"
    env["PATH"] = "/root/.local/bin:/usr/local/bin:/usr/bin:/bin"
    return env


def validate_codex_login() -> None:
    if not CODEX_PATH.is_file() or not os.access(CODEX_PATH, os.X_OK):
        raise CodexStop("Codex CLI binary missing or not executable")
    try:
        result = subprocess.run(
            [str(CODEX_PATH), "login", "status"], text=True, capture_output=True,
            timeout=30, env=codex_env(), cwd="/root/agenteos",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexStop(f"Codex login status unavailable: {type(exc).__name__}") from exc
    status_text = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0 or "chatgpt" not in status_text.casefold():
        # Do not log stdout/stderr, which could contain sensitive auth detail.
        raise CodexStop("Codex ChatGPT authentication is unavailable")


def codex_metadata() -> dict[str, str]:
    result: dict[str, str] = {"codex_path": str(CODEX_PATH)}
    try:
        version = subprocess.run(
            [str(CODEX_PATH), "--version"], text=True, capture_output=True,
            timeout=15, env=codex_env(), cwd="/root/agenteos",
        )
        if version.returncode == 0:
            result["codex_version"] = version.stdout.strip().splitlines()[0][:120]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def parse_jsonl_final(stdout: str) -> str:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexRetryable(f"invalid JSONL event at line {line_number}") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise CodexRetryable(f"invalid JSONL event shape at line {line_number}")
        events.append(event)
    if not events:
        raise CodexRetryable("Codex returned no JSONL events")
    event_text = " ".join(json.dumps(event, ensure_ascii=False) for event in events).casefold()
    if any(token in event_text for token in ("usage limit", "rate limit", "limit reached", "quota exceeded", "too many requests", "account limit", "authentication", "unauthorized", "not logged in", "login required", "no credentials")):
        raise CodexStop("Codex reported account limit or authentication failure")
    failed = [event for event in events if event.get("type") in {"turn.failed", "error"}]
    if failed:
        detail = json.dumps(failed[-1], ensure_ascii=False).casefold()
        if any(token in detail for token in ("usage limit", "rate limit", "limit reached", "quota exceeded", "too many requests", "authentication", "unauthorized", "not logged in", "login required", "no credentials")):
            raise CodexStop("Codex reported account limit or authentication failure")
        raise CodexRetryable("Codex reported a failed turn")
    completed_turn = next((event for event in reversed(events) if event.get("type") == "turn.completed"), None)
    if not completed_turn:
        raise CodexRetryable("Codex final turn completion event missing")
    for event in reversed(events):
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                return item["text"]
    raise CodexRetryable("Codex final agent response missing")


def response_json(raw: str) -> dict[str, Any]:
    try:
        result = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise ValueError("final agent response is not a JSON object") from exc
    if not isinstance(result, dict):
        raise ValueError("model response is not a JSON object")
    missing = IMAGE_FIELDS - set(result)
    if missing:
        raise ValueError("model response missing fields: " + ",".join(sorted(missing)))
    string_fields = IMAGE_FIELDS - {"secondary_muscle_groups", "name_confidence", "muscle_confidence", "equipment_confidence", "variation_confidence"}
    for field in string_fields:
        if not isinstance(result[field], str):
            raise ValueError(f"invalid {field}")
    for field in string_fields:
        result[field] = result[field].strip()
    for field in ("name_confidence", "muscle_confidence", "equipment_confidence", "variation_confidence"):
        if not isinstance(result[field], str):
            raise ValueError(f"invalid {field}")
        result[field] = result[field].upper().strip()
        if result[field] not in CONFIDENCE:
            raise ValueError(f"invalid {field}")
    if not isinstance(result["secondary_muscle_groups"], list):
        raise ValueError("secondary_muscle_groups must be an array")
    if not all(isinstance(value, str) for value in result["secondary_muscle_groups"]):
        raise ValueError("secondary_muscle_groups must contain strings")
    return result


def validate_semantics(result: dict[str, Any]) -> None:
    minimums = {"visual_observation": 25, "visual_interpretation": 15, "exercise_name": 3}
    for field, minimum in minimums.items():
        value = result[field].strip()
        if len(value) < minimum or GENERIC.fullmatch(value):
            raise ReviewRequired("insufficient specific visual response")
    if not result["primary_muscle_group"].strip() or not result["equipment"].strip():
        raise ValueError("primary muscle group and equipment must be non-empty; use 'Não identificado' if unclear")


def validate_visual_evidence(result: dict[str, Any]) -> None:
    for field, minimum in (("visual_observation", 25), ("visual_interpretation", 15)):
        value = result[field].strip()
        if len(value) < minimum or GENERIC.fullmatch(value):
            raise ReviewRequired("insufficient specific visual evidence")


def evaluate_test_samples(ids: list[str]) -> bool:
    rows = {row["catalog_entry_id"]: row for row in read_csv(REVIEW_CSV)}
    vision_dir = ROOT / "vision_results"
    for entry_id in ids:
        record = rows.get(entry_id)
        raw_result = vision_dir / f"{entry_id}.json"
        if (not record or record.get("visual_method") != VISUAL_METHOD
                or record.get("persist_status") not in {"persisted", "review_required"}
                or not raw_result.is_file()):
            return False
        saved = json.loads(raw_result.read_text(encoding="utf-8"))
        try:
            vision = response_json(json.dumps(saved.get("vision"), ensure_ascii=False))
        except (ValueError, TypeError):
            return False
        if saved.get("visual_method") != VISUAL_METHOD:
            return False
        execution = saved.get("codex_execution") or {}
        attachment = Path(str(execution.get("image_attachment") or ""))
        if (execution.get("event") != "turn.completed" or execution.get("prompt_source") != "stdin"
                or not attachment.is_file() or attachment.parent.parent != ROOT / "worker_tmp"
                or not re.fullmatch(r"[0-9a-f]{32}", attachment.parent.name)
                or hashlib.sha256(attachment.read_bytes()).hexdigest() != execution.get("image_sha256")
                or "-i" not in execution.get("argv_pattern", []) or "-" not in execution.get("argv_pattern", [])):
            return False
        try:
            if record.get("persist_status") == "review_required":
                # An ambiguous clip is an expected outcome: require detailed visual
                # evidence, low/none naming confidence, and an explicit unresolved label.
                validate_visual_evidence(vision)
                if (not saved.get("review_required")
                        or vision["name_confidence"] not in {"LOW", "NONE"}
                        or not GENERIC.fullmatch(vision["exercise_name"].strip())):
                    return False
            else:
                if saved.get("review_required"):
                    return False
                validate_semantics(vision)
        except ReviewRequired:
            return False
        if (record.get("visual_observation") != vision["visual_observation"]
                or record.get("visual_interpretation") != vision["visual_interpretation"]):
            return False
    return bool(ids) and len(ids) == len(set(ids))


def vision_prompt(*, focused_retry: bool = False) -> str:
    retry_instruction = (
        "Faça uma análise visual ainda mais concreta: acompanhe as posições inicial, intermediária e final; "
        "nomeie a trajetória dos membros e o objeto/equipamento realmente visível. Se a imagem não sustentar "
        "uma identificação, registre observação específica e confidence LOW ou NONE. "
        if focused_retry else ""
    )
    return retry_instruction + """Inspecione primeiro e somente o conteúdo visual da imagem anexada. Não use nomes de arquivo ou pastas como pista.
Não há contexto do catálogo nesta etapa. Não invente dados e não leia texto/OCR.
Responda SOMENTE com um objeto JSON válido, sem markdown, contendo:
visual_observation, visual_interpretation, exercise_name, primary_muscle_group,
secondary_muscle_groups (array de strings), equipment, movement_pattern,
body_position, laterality, grip, limb_path, angle_or_orientation,
machine_or_free_weight, notable_variation, name_confidence, muscle_confidence,
equipment_confidence, variation_confidence, description e instructions.
visual_observation deve conter somente fatos visíveis e detalhes concretos da posição corporal,
movimento/trajectória dos membros, equipamento e unilateralidade/bilateralidade quando visível.
visual_interpretation deve identificar o movimento a partir da imagem. Confidence deve ser HIGH,
MEDIUM, LOW ou NONE. Para campo incerto use string vazia ou 'Não identificado' e reduza a confiança.
Description e instructions devem ser curtas e sustentadas pelo movimento observado."""


def invoke_codex(image_path: Path, *, focused_retry: bool = False) -> tuple[str, dict[str, Any]]:
    if not image_path.is_file():
        raise CodexRetryable("Codex image attachment file is missing")
    command = [str(CODEX_PATH), "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "--json", "--color", "never", "-i", str(image_path), "-"]
    invoked_at = now()
    started = time.monotonic()
    try:
        result = subprocess.run(
            command, input=vision_prompt(focused_retry=focused_retry), text=True,
            capture_output=True, timeout=CODEX_TIMEOUT_SECONDS, env=codex_env(),
            cwd="/root/agenteos",
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexRetryable(f"codex exec timed out after {CODEX_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise CodexStop(f"codex exec could not start: {type(exc).__name__}") from exc
    combined = (result.stdout + "\n" + result.stderr).casefold()
    if any(token in combined for token in ("usage limit", "rate limit", "limit reached", "quota exceeded", "too many requests", "account limit", "authentication", "unauthorized", "not logged in", "login required", "no credentials")):
        raise CodexStop("Codex reported account limit or authentication failure")
    if result.returncode != 0:
        if any(token in combined for token in ("service unavailable", "temporarily unavailable", "overloaded")):
            raise CodexRetryable("Codex service temporarily unavailable")
        raise CodexRetryable(f"codex exec exited with status {result.returncode}")
    final_text = parse_jsonl_final(result.stdout)
    evidence = {
        "invocation_id": uuid.uuid4().hex,
        "invoked_at": invoked_at,
        "completed_at": now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "event": "turn.completed",
        "image_attachment": str(image_path),
        "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "argv_pattern": ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "--json", "--color", "never", "-i", "<neutral-image-path>", "-"],
        "prompt_source": "stdin",
    }
    log(f"codex_exec=turn.completed attachment={image_path} sha256={evidence['image_sha256']} duration={evidence['duration_seconds']:.1f}s")
    return final_text, evidence


def call_vision(image_path: Path, *, focused_retry: bool = False, entry_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    last: Exception | None = None
    for attempt in range(CODEX_ATTEMPTS):
        try:
            final_text, evidence = invoke_codex(image_path, focused_retry=focused_retry)
            parsed = response_json(final_text)
            try:
                validate_semantics(parsed)
            except ReviewRequired as exc:
                raise ReviewRequired(str(exc), parsed, evidence) from exc
            return parsed, evidence
        except (CodexStop, ReviewRequired):
            raise
        except (CodexRetryable, ValueError) as exc:
            last = exc
            if attempt + 1 < CODEX_ATTEMPTS:
                log(f"entry={entry_id or 'UNKNOWN'} vision=RETRY attempt={attempt + 1}/{CODEX_ATTEMPTS} reason={type(exc).__name__}")
                time.sleep(CODEX_RETRY_DELAY_SECONDS * (attempt + 1))
    raise CodexRetryable(f"Codex retries exhausted: {type(last).__name__ if last else 'unknown'}")


def load_catalog_comparison(entry_id: str) -> dict[str, Any]:
    query = """SELECT row_to_json(x)::text FROM (
      SELECT e.id::text AS entry_id,e.canonical_name,e.muscle_group,e.equipment,
             e.body_position,e.laterality,e.grip,e.variation,e.description,e.instructions,
             COALESCE((SELECT json_agg(a.alias ORDER BY a.alias) FROM public.leoft_catalog_aliases a
                       WHERE a.catalog_entry_id=e.id), '[]'::json) AS aliases,
             (SELECT row_to_json(r) FROM (
                 SELECT proposed_name,proposed_muscle_group,proposed_equipment
                 FROM leoft_ops.catalog_reviews cr WHERE cr.catalog_entry_id=e.id
                 ORDER BY cr.updated_at DESC LIMIT 1
             ) r) AS textual_proposal
      FROM public.leoft_catalog_entries e WHERE e.id=%s
    ) x""" % sqlq(entry_id)
    raw = psql(query)
    if not raw:
        raise RuntimeError("catalog comparison record missing")
    return json.loads(raw)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def generic_current(value: Any) -> bool:
    normalized = _norm(value)
    return not normalized or normalized.startswith(("nao identificado", "nao identificada", "nao informado", "nao informada")) or normalized in {"outro", "outra", "equipamento", "musculacao", "unknown", "none"}


def _same_value(candidate: Any, old: Any, aliases: list[str] | None = None, *, kind: str = "") -> bool:
    candidate_norm, old_norm = _norm(candidate), _norm(old)
    if candidate_norm == old_norm:
        return True
    alias_norms = {_norm(alias) for alias in aliases or []}
    if candidate_norm and candidate_norm in alias_norms:
        return True
    count_words = {"um", "uma", "dois", "duas", "tres", "quatro", "par", "de"}
    candidate_words = candidate_norm.split()
    while candidate_words and candidate_words[0] in count_words:
        candidate_words.pop(0)
    candidate_norm = " ".join(candidate_words)
    if candidate_norm == old_norm:
        return True
    # Small controlled synonym sets prevent replacing established values merely
    # to switch between common Portuguese/English spellings.
    synonym_groups = {
        "equipment": ({"halter", "halteres", "dumbbell", "dumbbells"}, {"barra", "barbell"}, {"peso corporal", "bodyweight", "peso do corpo"}, {"cabo", "polia", "cable"}, {"elastico", "faixa elastica", "resistance band"}),
        "muscle": ({"ombro", "ombros", "deltoide", "deltoides", "deltoide anterior", "deltoide lateral", "deltoide posterior", "shoulder", "shoulders"}, {"peitoral", "peito", "peitorais", "chest", "pectorals"}, {"costas", "dorsal", "back"}, {"gluteo", "gluteos", "glutes"}, {"quadriceps", "quadriceps femoral"}, {"posterior de coxa", "isquiotibiais", "hamstrings"}),
    }
    return any(candidate_norm in group and old_norm in group for group in synonym_groups.get(kind, ()))


def compare_and_changes(vision: dict[str, Any], current: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    changes: dict[str, Any] = {}
    aliases = [str(value) for value in (current.get("aliases") or [])]
    mapping = [
        ("exercise_name", "canonical_name", "name_confidence", "name"),
        ("primary_muscle_group", "muscle_group", "muscle_confidence", "muscle"),
        ("equipment", "equipment", "equipment_confidence", "equipment"),
    ]
    for src, dest, confidence_key, kind in mapping:
        candidate = str(vision.get(src) or "").strip()
        old = str(current.get(dest) or "").strip()
        confidence = vision[confidence_key]
        equivalent = _same_value(candidate, old, aliases if kind == "name" else None, kind=kind)
        if confidence == "HIGH" and candidate and not equivalent:
            changes[dest] = candidate
        elif confidence == "MEDIUM" and candidate and generic_current(old):
            changes[dest] = candidate

    # LOW/NONE never alters a specific value. Medium may only fill blanks or
    # values that are explicitly generic; high can refine specific fields.
    for src, dest in (("body_position", "body_position"), ("laterality", "laterality"), ("grip", "grip"), ("notable_variation", "variation")):
        candidate = str(vision.get(src) or "").strip()
        old = str(current.get(dest) or "").strip()
        confidence = vision["variation_confidence"]
        if candidate and confidence == "HIGH" and not _same_value(candidate, old):
            changes[dest] = candidate
        elif candidate and confidence == "MEDIUM" and generic_current(old):
            changes[dest] = candidate

    sufficient = vision["name_confidence"] in {"HIGH", "MEDIUM"} and vision["muscle_confidence"] in {"HIGH", "MEDIUM"}
    if sufficient:
        for src, dest in (("description", "description"), ("instructions", "instructions")):
            candidate = str(vision.get(src) or "").strip()
            if candidate and generic_current(current.get(dest)):
                changes[dest] = candidate

    old_values = (current.get("canonical_name"), current.get("muscle_group"), current.get("equipment"))
    visual_values = (vision["exercise_name"], vision["primary_muscle_group"], vision["equipment"])
    confs = (vision["name_confidence"], vision["muscle_confidence"], vision["equipment_confidence"])
    if any(conf in {"LOW", "NONE"} for conf in confs):
        classification = "UNRESOLVED"
    elif all(_same_value(candidate, old, aliases if idx == 0 else None, kind=("name", "muscle", "equipment")[idx]) for idx, (candidate, old) in enumerate(zip(visual_values, old_values))):
        classification = "UNCHANGED_CONFIRMED"
    elif any(generic_current(old) for old in old_values) and any(not generic_current(value) for value in visual_values):
        classification = "FILLED_MISSING"
    elif any(key in changes for key in ("canonical_name", "muscle_group", "equipment")):
        classification = "CORRECTED" if all(not generic_current(value) for value in old_values) else "REFINED"
    else:
        classification = "REFINED"
    return classification, changes


def load_existing_audit(entry_id: str) -> dict[str, Any] | None:
    raw = psql(
        "SELECT row_to_json(x)::text FROM (SELECT before_values,after_values "
        "FROM leoft_ops.catalog_review_audit WHERE catalog_entry_id=" + sqlq(entry_id) +
        " AND action=" + sqlq(RUN_ID) + " AND after_values->>'visual_method'=" + sqlq(VISUAL_METHOD) +
        " ORDER BY created_at LIMIT 1) x"
    )
    return json.loads(raw) if raw else None


def persist(entry_id: str, media: dict[str, Any], vision: dict[str, Any], current: dict[str, Any], comparison: str, changes: dict[str, Any], metadata: dict[str, str]) -> dict[str, Any]:
    editable_fields = ["canonical_name", "muscle_group", "equipment", "body_position", "laterality", "grip", "variation", "description", "instructions"]
    audit = load_existing_audit(entry_id)
    if audit:
        old = audit["before_values"]
        after = audit["after_values"]
        comparison = str(after.get("comparison") or comparison)
        changes = {key: after[key] for key in editable_fields if key in after and after.get(key) != old.get(key)}
    else:
        old = {key: current.get(key) for key in editable_fields}
        after = {
            **old, **changes,
            "visual_observation": vision["visual_observation"],
            "visual_interpretation": vision["visual_interpretation"],
            "confidence": {key: vision[key] for key in ("name_confidence", "muscle_confidence", "equipment_confidence", "variation_confidence")},
            "visual_method": VISUAL_METHOD, "run_id": RUN_ID, "comparison": comparison,
            **metadata,
        }
        sets = [f"{key}={sqlq(value)}" for key, value in changes.items()]
        update_sql = ("UPDATE public.leoft_catalog_entries SET " + ",".join(sets) + ",updated_at=NOW() WHERE id=" + sqlq(entry_id) + ";") if sets else ""
        review_id = psql("SELECT id::text FROM leoft_ops.catalog_reviews WHERE catalog_entry_id=" + sqlq(entry_id) + " ORDER BY updated_at DESC LIMIT 1")
        if not review_id:
            raise RuntimeError("catalog_review_audit requires existing catalog_reviews.review_id")
        sql = (
            "BEGIN;" + update_sql +
            "INSERT INTO leoft_ops.catalog_review_audit(review_id,catalog_entry_id,action,before_values,after_values,created_at) "
            "SELECT " + sqlq(review_id) + "," + sqlq(entry_id) + "," + sqlq(RUN_ID) + "," + json_sql(old) + "," + json_sql(after) + ",NOW() "
            "WHERE NOT EXISTS (SELECT 1 FROM leoft_ops.catalog_review_audit WHERE catalog_entry_id=" + sqlq(entry_id) +
            " AND action=" + sqlq(RUN_ID) + " AND after_values->>'visual_method'=" + sqlq(VISUAL_METHOD) + ");COMMIT;"
        )
        psql(sql)

    timestamp = now()
    required = comparison == "UNRESOLVED" or any(vision[key] in {"LOW", "NONE"} for key in ("name_confidence", "muscle_confidence", "equipment_confidence", "variation_confidence"))
    row = {
        "catalog_entry_id": entry_id, "catalog_media_id": media["media_id"], "sha256": media["sha256"],
        "visual_observation": vision["visual_observation"], "visual_interpretation": vision["visual_interpretation"],
        "visual_method": VISUAL_METHOD, "old_name": old.get("canonical_name"),
        "new_name": changes.get("canonical_name", old.get("canonical_name")), "name_confidence": vision["name_confidence"],
        "old_muscle": old.get("muscle_group"), "new_muscle": changes.get("muscle_group", old.get("muscle_group")),
        "muscle_confidence": vision["muscle_confidence"], "old_equipment": old.get("equipment"),
        "new_equipment": changes.get("equipment", old.get("equipment")), "equipment_confidence": vision["equipment_confidence"],
        "variation": vision["notable_variation"], "variation_confidence": vision["variation_confidence"],
        "secondary_muscles": "; ".join(vision["secondary_muscle_groups"]), "movement_pattern": vision["movement_pattern"],
        "body_position": vision["body_position"], "laterality": vision["laterality"], "grip": vision["grip"],
        "limb_path": vision["limb_path"], "angle_or_orientation": vision["angle_or_orientation"],
        "notable_variation": vision["notable_variation"], "comparison": comparison,
        "database_changed": "SIM" if changes else "NAO", "persist_status": "review_required" if required else "persisted", "timestamp": timestamp, "error": "",
    }
    csv_upsert(REVIEW_CSV, REVIEW_FIELDS, row)
    if changes:
        csv_upsert(CHANGES_CSV, CHANGES_FIELDS, row)
    else:
        csv_remove_non_codex(CHANGES_CSV, entry_id)
        csv_remove_non_codex(REQUIRED_CSV, entry_id)
    if required:
        csv_upsert(REQUIRED_CSV, REQUIRED_FIELDS, row)
    return {**row, "review_required": required}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def csv_remove_non_codex(path: Path, entry_id: str) -> None:
    rows = read_csv(path)
    matching = [row for row in rows if row.get("catalog_entry_id") == entry_id]
    if not matching or any(row.get("visual_method") == VISUAL_METHOD for row in matching):
        return
    remaining = [row for row in rows if row.get("catalog_entry_id") != entry_id]
    tmp = path.with_suffix(path.suffix + ".tmp")
    fields = REQUIRED_FIELDS if path == REQUIRED_CSV else CHANGES_FIELDS
    with tmp.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(remaining)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def process_one(state: dict[str, Any], entry_id: str, ordinal: int, *, force_vision: bool = False, preserve_evidence: bool = False) -> None:
    vision_dir = ROOT / "vision_results"
    vision_dir.mkdir(parents=True, exist_ok=True)
    vision_file = vision_dir / f"{entry_id}.json"
    work_dir: Path | None = None
    started = time.monotonic()
    prepare_status = "OK"
    vision_status = "OK"
    state["status_by_id"][entry_id] = "processing"
    state["processing_ids"] = [entry_id]
    state["last_entry_id"] = entry_id
    save_progress(state)

    try:
        media = load_media(entry_id)
        source = resolve_and_validate_media(media)
        unresolved = False
        if vision_file.is_file() and not force_vision:
            saved = json.loads(vision_file.read_text(encoding="utf-8"))
            if saved.get("run_id") != RUN_ID or saved.get("catalog_entry_id") != entry_id or saved.get("catalog_media_id") != media["media_id"] or saved.get("sha256") != media["sha256"]:
                raise ItemFailure("saved visual result does not match catalog media/run")
            vision_execution = saved.get("codex_execution") or {}
            if saved.get("visual_method") != VISUAL_METHOD or vision_execution.get("event") != "turn.completed":
                raise WorkerStop("saved visual result has no completed Codex image attachment")
            vision = response_json(json.dumps(saved.get("vision"), ensure_ascii=False))
            unresolved = bool(saved.get("review_required"))
            if not unresolved:
                validate_semantics(vision)
            prepare_status = "RECOVERED"
        else:
            work_dir = ROOT / "worker_tmp" / uuid.uuid4().hex
            _, image = prepare_image(entry_id, media, source=source, work_dir=work_dir)
            state["frames_ready_ids"] = list(dict.fromkeys(state["frames_ready_ids"] + [entry_id]))
            save_progress(state)
            try:
                vision, vision_execution = call_vision(image, entry_id=entry_id)
            except ReviewRequired as first:
                log(f"[{ordinal}/1074] entry={entry_id} vision=RETRY reason=generic_response")
                try:
                    vision, vision_execution = call_vision(image, focused_retry=True, entry_id=entry_id)
                except ReviewRequired as second:
                    vision = second.result or first.result
                    if not vision:
                        raise CodexRetryable("generic Codex response did not contain recoverable visual JSON")
                    vision_execution = second.evidence or first.evidence
                    unresolved = True
            saved = {
                "run_id": RUN_ID, "catalog_entry_id": entry_id, "catalog_media_id": media["media_id"],
                "sha256": media["sha256"], "visual_method": VISUAL_METHOD,
                "created_at": now(), "review_required": unresolved, "vision": vision,
                "contact_sheet_path": str(image) if image != source else None,
                "codex_execution": vision_execution,
            }
            atomic_json(vision_file, saved)

        if not unresolved:
            state["visually_inspected_ids"] = list(dict.fromkeys(state["visually_inspected_ids"] + [entry_id]))
            state["status_by_id"][entry_id] = "visually_inspected"
            save_progress(state)

        # Ordering is deliberate: durable raw visual JSON exists before these
        # catalog names, aliases, equipment or textual proposals are queried.
        current = load_catalog_comparison(entry_id)
        if unresolved:
            outcome = write_review_required(entry_id, media, vision, current, "Codex permaneceu genérico após a tentativa visual focada")
            state["pending_ids"] = [value for value in state["pending_ids"] if value != entry_id]
            state["processing_ids"] = []
            state["status_by_id"][entry_id] = "review_required"
            state["visually_inspected_ids"] = list(dict.fromkeys(state["visually_inspected_ids"] + [entry_id]))
            state["persisted_ids"] = [value for value in state["persisted_ids"] if value != entry_id]
            state.setdefault("review_required_ids", [])
            if entry_id not in state["review_required_ids"]:
                state["review_required_ids"].append(entry_id)
            state["last_completed_entry_id"] = entry_id
            save_progress(state)
            log(f"[{ordinal}/1074] entry={entry_id} prepare={prepare_status} vision=REVIEW_REQUIRED update=NO duration={time.monotonic()-started:.1f}s")
            return

        comparison, changes = compare_and_changes(vision, current)
        metadata = dict(state.get("codex_metadata") or {})
        outcome = persist(entry_id, media, vision, current, comparison, changes, metadata)
        state["pending_ids"] = [value for value in state["pending_ids"] if value != entry_id]
        state["processing_ids"] = []
        state["status_by_id"][entry_id] = "review_required" if outcome["review_required"] else "persisted"
        if outcome["review_required"]:
            state["persisted_ids"] = [value for value in state["persisted_ids"] if value != entry_id]
            state.setdefault("review_required_ids", [])
            if entry_id not in state["review_required_ids"]:
                state["review_required_ids"].append(entry_id)
        else:
            state["persisted_ids"] = list(dict.fromkeys(state["persisted_ids"] + [entry_id]))
            state["review_required_ids"] = [value for value in state.get("review_required_ids", []) if value != entry_id]
        state["last_completed_entry_id"] = entry_id
        state["last_completed_batch"] = f"worker_{(len(state['persisted_ids']) + 9) // 10:04d}"
        save_progress(state)
        review_label = " review_required" if outcome["review_required"] else ""
        log(f"[{ordinal}/1074] entry={entry_id} prepare={prepare_status} vision={vision_status} update={'YES' if changes else 'NO'}{review_label} duration={time.monotonic()-started:.1f}s method={VISUAL_METHOD}")
        # Preserve contact sheets for review; remove only normal transient frames.
        if not outcome["review_required"] and not preserve_evidence:
            shutil.rmtree(work_dir, ignore_errors=True)
    except CodexRetryable as exc:
        # Exhausted retries are isolated to this entry. The bounded retries in
        # call_vision already handled transient CLI/JSON failures; keep the run
        # moving while retaining a durable FAILED checkpoint for follow-up.
        state["processing_ids"] = []
        state["status_by_id"][entry_id] = "failed"
        state["failed_ids"] = list(dict.fromkeys(state["failed_ids"] + [entry_id]))
        state["pending_ids"] = [value for value in state["pending_ids"] if value != entry_id]
        save_progress(state)
        log(f"[{ordinal}/1074] entry={entry_id} status=failed retry_exhausted={type(exc).__name__}: {str(exc)[:200]}")
    except (CodexStop, WorkerStop) as exc:
        state["processing_ids"] = []
        state["status_by_id"][entry_id] = "visually_inspected" if vision_file.is_file() else "pending"
        if entry_id not in state["pending_ids"] and entry_id not in state["persisted_ids"]:
            state["pending_ids"].insert(0, entry_id)
        save_progress(state)
        log(f"[{ordinal}/1074] entry={entry_id} vision=STOP reason={type(exc).__name__}: {str(exc)[:240]} duration={time.monotonic()-started:.1f}s")
        raise WorkerStop(str(exc)) from exc
    except ItemFailure as exc:
        state["processing_ids"] = []
        state["status_by_id"][entry_id] = "failed"
        state["failed_ids"] = list(dict.fromkeys(state["failed_ids"] + [entry_id]))
        state["pending_ids"] = [value for value in state["pending_ids"] if value != entry_id]
        save_progress(state)
        log(f"[{ordinal}/1074] entry={entry_id} status=failed reason={str(exc)[:240]}")
    except Exception as exc:
        state["processing_ids"] = []
        state["status_by_id"][entry_id] = "visually_inspected" if vision_file.is_file() else "pending"
        if entry_id not in state["pending_ids"] and entry_id not in state["persisted_ids"]:
            state["pending_ids"].insert(0, entry_id)
        save_progress(state)
        log(f"[{ordinal}/1074] entry={entry_id} safe_stop={type(exc).__name__}: {str(exc)[:240]}")
        raise WorkerStop(f"item processing stopped safely: {type(exc).__name__}") from exc


def resolve_and_validate_media(media: dict[str, Any]) -> Path:
    source = (MEDIA_ROOT / str(media["storage_path"])).resolve()
    if MEDIA_ROOT.resolve() not in source.parents or not source.is_file():
        raise ItemFailure("media file missing or outside canonical media root")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != str(media["sha256"]).lower():
        raise ItemFailure("SHA-256 mismatch")
    return source


def write_review_required(entry_id: str, media: dict[str, Any], vision: dict[str, Any], current: dict[str, Any], reason: str) -> dict[str, Any]:
    old_name = str(current.get("canonical_name") or "")
    old_muscle = str(current.get("muscle_group") or "")
    old_equipment = str(current.get("equipment") or "")
    timestamp = now()
    row = {
        "catalog_entry_id": entry_id, "catalog_media_id": media["media_id"], "sha256": media["sha256"],
        "visual_observation": vision["visual_observation"], "visual_interpretation": vision["visual_interpretation"],
        "visual_method": VISUAL_METHOD, "old_name": old_name, "new_name": old_name,
        "name_confidence": vision["name_confidence"], "old_muscle": old_muscle, "new_muscle": old_muscle,
        "muscle_confidence": vision["muscle_confidence"], "old_equipment": old_equipment, "new_equipment": old_equipment,
        "equipment_confidence": vision["equipment_confidence"], "variation": vision["notable_variation"],
        "variation_confidence": vision["variation_confidence"], "secondary_muscles": "; ".join(vision["secondary_muscle_groups"]),
        "movement_pattern": vision["movement_pattern"], "body_position": vision["body_position"],
        "laterality": vision["laterality"], "grip": vision["grip"], "limb_path": vision["limb_path"],
        "angle_or_orientation": vision["angle_or_orientation"], "notable_variation": vision["notable_variation"],
        "comparison": "UNRESOLVED", "database_changed": "NAO", "persist_status": "review_required",
        "timestamp": timestamp, "error": reason,
    }
    csv_upsert(REVIEW_CSV, REVIEW_FIELDS, row)
    csv_upsert(REQUIRED_CSV, REQUIRED_FIELDS, row)
    return row


def status() -> None:
    state = load_progress()
    partition = exclusive_partition(state)
    changes = read_csv(CHANGES_CSV)
    total_changed = len({r["catalog_entry_id"] for r in changes if r.get("visual_method") == VISUAL_METHOD})
    values = {
        "TOTAL": state["total"], "UNIQUE_IDS": len(partition["ordered_ids"]),
        "PERSISTED": len(partition["persisted"]),
        "PENDING": len(partition["pending"]), "PROCESSING": len(partition["processing"]),
        "REVIEW_REQUIRED": len(partition["review_required"]), "FAILED": len(partition["failed"]),
        "ACCOUNTED_TOTAL": sum(len(partition[key]) for key in ("persisted", "pending", "processing", "review_required", "failed")),
        "VISUALLY_ANALYZED": len(partition["persisted"] | partition["review_required"]),
        "DATABASE_CHANGED": total_changed,
        "LAST_COMPLETED_ENTRY": state.get("last_completed_entry_id") or "NONE",
        "NEXT_PENDING_ENTRY": next((entry_id for entry_id in partition["ordered_ids"] if entry_id in partition["pending"]), "NONE"),
        "UPDATED_AT": state.get("updated_at") or "UNKNOWN",
    }
    for key, value in values.items():
        print(f"{key}={value}")
    automated_done = not partition["pending"] and not partition["processing"] and not partition["failed"]
    print(f"AUTOMATED_VISUAL_REVIEW_COMPLETED={'SIM' if automated_done else 'NAO'}")
    print(f"FULL_CATALOG_REVIEW_RESOLVED={'SIM' if automated_done and not partition['review_required'] else 'NAO'}")


def valid_codex_result(entry_id: str, *, require_attachment_file: bool = False) -> bool:
    result_file = ROOT / "vision_results" / f"{entry_id}.json"
    try:
        saved = json.loads(result_file.read_text(encoding="utf-8"))
        execution = saved.get("codex_execution") or {}
        attachment = Path(str(execution.get("image_attachment") or ""))
        if (saved.get("run_id") != RUN_ID or saved.get("catalog_entry_id") != entry_id
                or saved.get("visual_method") != VISUAL_METHOD
                or execution.get("event") != "turn.completed" or execution.get("prompt_source") != "stdin"
                or "-i" not in execution.get("argv_pattern", []) or "-" not in execution.get("argv_pattern", [])
                or not re.fullmatch(r"[0-9a-f]{32}", str(execution.get("invocation_id") or ""))
                or not re.fullmatch(r"[0-9a-f]{64}", str(execution.get("image_sha256") or ""))):
            return False
        if require_attachment_file and (
            not attachment.is_file() or attachment.parent.parent != ROOT / "worker_tmp"
            or not re.fullmatch(r"[0-9a-f]{32}", attachment.parent.name)
            or hashlib.sha256(attachment.read_bytes()).hexdigest() != execution.get("image_sha256")
        ):
            return False
        vision = response_json(json.dumps(saved.get("vision"), ensure_ascii=False))
        if saved.get("review_required"):
            validate_visual_evidence(vision)
            return vision["name_confidence"] in {"LOW", "NONE"} and bool(GENERIC.fullmatch(vision["exercise_name"].strip()))
        validate_semantics(vision)
        return True
    except (OSError, TypeError, ValueError, ReviewRequired, json.JSONDecodeError):
        return False


def exclusive_partition(state: dict[str, Any], *, recover_orphans: bool = False) -> dict[str, Any]:
    manifest = read_csv(RECOVERY_MANIFEST)
    ordered_ids = [row.get("catalog_entry_id", "") for row in manifest]
    expected = set(ordered_ids)
    if len(ordered_ids) != 1074 or len(expected) != 1074 or "" in expected:
        raise RuntimeError("run manifest does not contain exactly 1074 unique catalog IDs")
    review_rows = {row.get("catalog_entry_id"): row for row in read_csv(REVIEW_CSV)}
    persisted_rows = {
        entry_id for entry_id, row in review_rows.items()
        if row.get("visual_method") == VISUAL_METHOD and row.get("persist_status") == "persisted"
        and valid_codex_result(entry_id)
    }
    audited_raw = psql(
        "SELECT DISTINCT catalog_entry_id::text FROM leoft_ops.catalog_review_audit WHERE action="
        + sqlq(RUN_ID) + " AND after_values->>'visual_method'=" + sqlq(VISUAL_METHOD)
    )
    audited = set(audited_raw.splitlines())
    persisted = (persisted_rows & audited) & expected
    review_required = (set(state.get("review_required_ids", [])) | {
        entry_id for entry_id, value in state.get("status_by_id", {}).items() if value == "review_required"
    }) & expected
    # Human-review status has priority over an earlier database write. That write
    # remains intact and auditable, while the item stays out of the auto queue.
    persisted -= review_required
    failed = (set(state.get("failed_ids", [])) | {
        entry_id for entry_id, value in state.get("status_by_id", {}).items() if value == "failed"
    }) & expected
    failed -= persisted | review_required
    old_processing = (set(state.get("processing_ids", [])) | {
        entry_id for entry_id, value in state.get("status_by_id", {}).items() if value == "processing"
    }) & expected
    processing: set[str] = set()
    recovered_orphans: set[str] = set()
    for entry_id in old_processing - persisted - review_required - failed:
        row = review_rows.get(entry_id, {})
        if (row.get("visual_method") == VISUAL_METHOD and row.get("persist_status") == "persisted"
                and entry_id in audited and valid_codex_result(entry_id)):
            persisted.add(entry_id)
        elif (row.get("visual_method") == VISUAL_METHOD and row.get("persist_status") == "review_required"
              and valid_codex_result(entry_id)):
            review_required.add(entry_id)
        elif recover_orphans:
            recovered_orphans.add(entry_id)
        else:
            processing.add(entry_id)
        # On startup, an incomplete orphan returns to PENDING. process_one can
        # resume a durable raw result without repeating the Codex call.
    failed -= persisted | review_required
    pending = expected - persisted - review_required - failed - processing
    if set().union(persisted, pending, processing, review_required, failed) != expected:
        raise RuntimeError("exclusive run state does not reconcile to the manifest")
    return {
        "ordered_ids": ordered_ids, "persisted": persisted, "pending": pending,
        "processing": processing, "review_required": review_required, "failed": failed,
        "database_persisted": audited & expected,
        "recovered_orphans": recovered_orphans,
    }


def normalize_checkpoint(state: dict[str, Any], partition: dict[str, Any]) -> None:
    old_persisted = list(state.get("persisted_ids", []))
    database_persisted = partition["database_persisted"]
    state["legacy_checkpoint_persisted_ids"] = list(dict.fromkeys(
        state.get("legacy_checkpoint_persisted_ids", []) + old_persisted
    ))
    state["database_persisted_ids"] = [entry_id for entry_id in partition["ordered_ids"] if entry_id in database_persisted]
    state["persisted_ids"] = [entry_id for entry_id in partition["ordered_ids"] if entry_id in partition["persisted"]]
    state["pending_ids"] = [entry_id for entry_id in partition["ordered_ids"] if entry_id in partition["pending"]]
    state["processing_ids"] = []
    state["review_required_ids"] = [entry_id for entry_id in partition["ordered_ids"] if entry_id in partition["review_required"]]
    state["failed_ids"] = [entry_id for entry_id in partition["ordered_ids"] if entry_id in partition["failed"]]
    state["visually_inspected_ids"] = [entry_id for entry_id in partition["ordered_ids"] if entry_id in partition["persisted"] | partition["review_required"]]
    state["worker_initial_inspected_ids"] = list(state["visually_inspected_ids"])
    state["status_by_id"] = {
        entry_id: next(name for name in ("persisted", "pending", "processing", "review_required", "failed") if entry_id in partition[name])
        for entry_id in partition["ordered_ids"]
    }


def validate_final_catalog_integrity() -> dict[str, Any]:
    if not RECOVERY_MANIFEST.is_file():
        raise WorkerStop("baseline catalog recovery manifest is missing")
    manifest_rows = read_csv(RECOVERY_MANIFEST)
    baseline = {row["catalog_entry_id"]: row for row in manifest_rows}
    if len(manifest_rows) != 1074 or len(baseline) != 1074:
        raise WorkerStop("baseline recovery manifest does not contain 1074 unique entries")
    id_list = ",".join(sqlq(entry_id) for entry_id in baseline)
    query = f"""SELECT json_build_object(
      'catalog_entry_count',(SELECT count(*) FROM public.leoft_catalog_entries),
      'catalog_media_count',(SELECT count(*) FROM public.leoft_catalog_media),
      'rows',COALESCE((SELECT json_agg(row_to_json(x) ORDER BY x.entry_id) FROM (
        SELECT e.id::text AS entry_id,m.id::text AS media_id,m.catalog_entry_id::text AS media_entry_id,
               m.sha256,m.storage_path
        FROM public.leoft_catalog_entries e
        LEFT JOIN public.leoft_catalog_media m ON m.catalog_entry_id=e.id
        WHERE e.id::text IN ({id_list})
      ) x),'[]'::json)
    )::text"""
    raw = psql(query)
    result = json.loads(raw or "{}")
    rows = result.get("rows") or []
    if len(rows) != 1074 or len({row.get("entry_id") for row in rows}) != 1074 or len({row.get("media_id") for row in rows}) != 1074:
        raise WorkerStop("the 1074 run entries/media do not reconcile to unique catalog IDs")
    for row in rows:
        entry_id = row.get("entry_id")
        original = baseline.get(entry_id)
        if not original or not row.get("media_id") or row.get("media_entry_id") != entry_id:
            raise WorkerStop("catalog media link differs from the preserved baseline")
        if row["media_id"] != original.get("catalog_media_id") or str(row.get("sha256", "")).lower() != str(original.get("expected_sha256", "")).lower():
            raise WorkerStop("catalog media ID/SHA differs from the preserved baseline")
        media = {"storage_path": row["storage_path"], "sha256": row["sha256"]}
        resolve_and_validate_media(media)
    return {
        "entries": len(rows), "media": len(rows),
        "catalog_entries_total": result.get("catalog_entry_count"),
        "catalog_media_total": result.get("catalog_media_count"),
        "sha256_and_links": "PASS",
    }


def _http_status(url: str, *, allow_redirect: bool = False) -> tuple[int, str, str]:
    # Keep redirects visible so protected screens can be recognized as live
    # even when this service process has no browser session cookie.
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    class StopRedirects(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = build_opener() if allow_redirect else build_opener(StopRedirects)
    try:
        with opener.open(Request(url, headers={"User-Agent": "leoft-visual-review-final-check/1.0"}), timeout=15) as response:
            return response.status, response.read(2048).decode("utf-8", "replace"), response.headers.get("Location", "")
    except HTTPError as exc:
        return exc.code, exc.read(2048).decode("utf-8", "replace"), exc.headers.get("Location", "")
    except URLError as exc:
        raise WorkerStop(f"final endpoint check unavailable: {type(exc).__name__}") from exc


def validate_application_routes() -> dict[str, str]:
    results: dict[str, str] = {}
    for path in ("/leoft-painel/exercicios", "/leoft-painel/treinos", "/leoft-painel/alunos"):
        code, _, location = _http_status(PANEL_BASE_URL + path)
        if not (200 <= code < 300 or (300 <= code < 400 and "/leoft-painel/login" in location)):
            raise WorkerStop(f"application route check failed for {path}: HTTP {code}")
        results[path] = f"HTTP_{code}"
    code, _, location = _http_status(PANEL_BASE_URL + "/leoft-painel/aluno/")
    if not (200 <= code < 300 or (300 <= code < 400 and "/leoft-painel/login" in location)):
        raise WorkerStop(f"public student UI route check failed: HTTP {code}")
    results["/leoft-painel/aluno/"] = f"HTTP_{code}"
    code, body, _ = _http_status(BACKEND_BASE_URL + "/api/public/leoft-app/access/session?token=codex-vision-probe-0001")
    if code != 404 or "Acesso inválido" not in body:
        raise WorkerStop(f"public student access API did not return its expected invalid-token response: HTTP {code}")
    results["/api/public/leoft-app/access/session"] = "HTTP_404_EXPECTED_INVALID_TOKEN"
    return results


def append_final_report(state: dict[str, Any], integrity: dict[str, Any], routes: dict[str, str]) -> None:
    review_rows = read_csv(REVIEW_CSV)
    changed_rows = read_csv(CHANGES_CSV)
    section = (
        "\n\n## Conclusão automática da revisão visual via Codex CLI — 2026-10-01\n\n"
        f"RUN_ID={RUN_ID}\n"
        f"VISUAL_METHOD={VISUAL_METHOD}\n"
        f"CODEX_VERSION={(state.get('codex_metadata') or {}).get('codex_version', 'não informado pelo CLI')}\n"
        f"TOTAL_EXERCISES={state['total']}\n"
        f"REAL_VISUALLY_INSPECTED={len(state['visually_inspected_ids'])}\n"
        f"PENDING={len(state['pending_ids'])}\n"
        f"REVIEW_REQUIRED={len(state.get('review_required_ids', []))}\n"
        f"FAILED={len(state['failed_ids'])}\n"
        f"PERSISTED={len(state['persisted_ids'])}\n"
        f"CHANGED_RECORDS={len({row['catalog_entry_id'] for row in changed_rows if row.get('visual_method') == VISUAL_METHOD})}\n"
        f"REVIEW_CSV_ROWS={len({row['catalog_entry_id'] for row in review_rows})}\n"
        f"RUN_MANIFEST_ENTRIES={integrity['entries']}\nRUN_MANIFEST_MEDIA={integrity['media']}\n"
        f"CATALOG_ENTRIES_TOTAL={integrity['catalog_entries_total']}\nCATALOG_MEDIA_TOTAL={integrity['catalog_media_total']}\n"
        f"MEDIA_SHA256_AND_LINKS={integrity['sha256_and_links']}\n"
        "AUTOMATED_VISUAL_REVIEW_COMPLETED=SIM\n"
        f"FULL_CATALOG_REVIEW_RESOLVED={'NAO' if state.get('review_required_ids') else 'SIM'}\n"
        "APPLICATION_ROUTE_CHECKS=" + json.dumps(routes, ensure_ascii=False, sort_keys=True) + "\n"
        "BACKGROUND_SERVICE=leoft-real-visual-review.service\n"
    )
    existing = FINAL_REPORT.read_text(encoding="utf-8") if FINAL_REPORT.is_file() else ""
    if "## Conclusão automática da revisão visual via Codex CLI" not in existing:
        FINAL_REPORT.parent.mkdir(parents=True, exist_ok=True)
        with FINAL_REPORT.open("a", encoding="utf-8") as stream:
            stream.write(section)
            stream.flush()
            os.fsync(stream.fileno())


def finalize_run(state: dict[str, Any]) -> bool:
    if state["pending_ids"] or state["failed_ids"]:
        return False
    if len(set(state["visually_inspected_ids"])) != int(state["total"]):
        log("final validation held: not all catalog entries have a specific visual interpretation")
        state["final_validation"] = "REVIEW_REQUIRED"
        save_progress(state)
        return False
    initial_inspected = set(state.get("worker_initial_inspected_ids", []))
    for entry_id in set(state["visually_inspected_ids"]) - initial_inspected:
        raw_file = ROOT / "vision_results" / f"{entry_id}.json"
        if not raw_file.is_file():
            raise WorkerStop("a newly inspected entry has no durable Codex vision result")
        saved = json.loads(raw_file.read_text(encoding="utf-8"))
        execution = saved.get("codex_execution") or {}
        if (saved.get("run_id") != RUN_ID or saved.get("visual_method") != VISUAL_METHOD
                or execution.get("event") != "turn.completed" or execution.get("prompt_source") != "stdin"
                or not re.fullmatch(r"[0-9a-f]{32}", str(execution.get("invocation_id") or ""))
                or not re.fullmatch(r"[0-9a-f]{64}", str(execution.get("image_sha256") or ""))
                or "-i" not in execution.get("argv_pattern", []) or "-" not in execution.get("argv_pattern", [])):
            raise WorkerStop("a newly inspected entry lacks proof of completed Codex image attachment")
        vision = response_json(json.dumps(saved.get("vision"), ensure_ascii=False))
        try:
            if saved.get("review_required"):
                validate_visual_evidence(vision)
                if vision["name_confidence"] not in {"LOW", "NONE"} or not GENERIC.fullmatch(vision["exercise_name"].strip()):
                    raise WorkerStop("review-required result does not preserve its unresolved classification")
            else:
                validate_semantics(vision)
        except ReviewRequired as exc:
            raise WorkerStop("a newly inspected entry does not contain specific visual evidence") from exc
    if len(set(state["persisted_ids"]) | set(state.get("review_required_ids", []))) != int(state["total"]):
        log("final validation held: completed/persisted IDs do not total 1074")
        return False
    integrity = validate_final_catalog_integrity()
    routes = validate_application_routes()
    append_final_report(state, integrity, routes)
    state["final_validation"] = "PASS"
    state["final_validation_at"] = now()
    save_progress(state)
    log("FINAL_VALIDATION=PASS entries=1074 media=1074 hashes_and_links=PASS routes=PASS report=updated")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="reporta checkpoint sem tocar no worker")
    parser.add_argument("--test", action="store_true", help="testa três pendentes e para")
    parser.add_argument("--recovery-test", action="store_true", help="processa um único PENDING novo e valida toda a persistência")
    parser.add_argument("--entry-id", help="ID opcional do PENDING a usar com --recovery-test")
    parser.add_argument("--evaluate-test", action="store_true", help="avalia amostras Codex já executadas sem processar itens")
    parser.add_argument("--finalize-only", action="store_true", help="revalida o run concluído e atualiza o relatório final")
    parser.add_argument("--max-items", type=int, default=0, help="limite opcional; 0 processa todas as pendentes")
    args = parser.parse_args()
    if args.status:
        status()
        return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("worker já está em execução", file=sys.stderr)
            return 0
        if args.finalize_only:
            state = load_progress()
            partition = exclusive_partition(state, recover_orphans=True)
            if partition["pending"] or partition["processing"] or partition["failed"]:
                log("FINALIZE_ONLY=WAIT pending=" + str(len(partition["pending"])) + " processing=" + str(len(partition["processing"])) + " failed=" + str(len(partition["failed"])))
                return 0
            normalize_checkpoint(state, partition)
            save_progress(state)
            if finalize_run(state):
                log("FINALIZE_ONLY=PASS AUTOMATED_VISUAL_REVIEW_COMPLETED=SIM FULL_CATALOG_REVIEW_RESOLVED=" + ("SIM" if not state["review_required_ids"] else "NAO"))
                return 0
            log("FINALIZE_ONLY=BLOCKED; see run validation details")
            return 1
        if args.evaluate_test:
            state = load_progress()
            ids = list(state.get("worker_test_sample_ids") or [])
            valid = evaluate_test_samples(ids)
            state["worker_codex_vision_test"] = "PASS" if valid else "FAIL"
            save_progress(state)
            log(f"WORKER_CODEX_VISION_TEST={'PASS' if valid else 'FAIL'} sample={','.join(ids)} method={VISUAL_METHOD} evaluated_existing=true")
            return 0 if valid else 5
        try:
            validate_codex_login()
        except CodexStop as exc:
            log(f"worker stopped before checkpoint changes: {str(exc)}")
            return 0
        metadata = codex_metadata()
        state = load_progress()
        partition = exclusive_partition(state, recover_orphans=True)
        orphaned = sorted(partition["recovered_orphans"])
        normalize_checkpoint(state, partition)
        if not state.get("last_completed_entry_id"):
            completed_order = [entry_id for entry_id in partition["ordered_ids"] if entry_id in partition["persisted"] | partition["review_required"]]
            state["last_completed_entry_id"] = completed_order[-1] if completed_order else None
        if not state.get("worker_started_at"):
            state["worker_started_at"] = now()
        state["worker_backend"] = "CODEX_CLI"
        state["visual_method"] = VISUAL_METHOD
        state["codex_metadata"] = metadata
        save_progress(state)
        if orphaned:
            log(f"ORPHAN_PROCESSING_RECOVERED={len(orphaned)} ids={','.join(orphaned)}; complete DB/CSV evidence retained, incomplete entries returned to PENDING")
        limit = 1 if args.recovery_test else (3 if args.test else (args.max_items or None))
        initial_pending = list(state["pending_ids"])
        if args.recovery_test:
            candidates = [entry_id for entry_id in initial_pending if not (ROOT / "vision_results" / f"{entry_id}.json").is_file()]
            if args.entry_id:
                candidates = [entry_id for entry_id in candidates if entry_id == args.entry_id]
            if not candidates:
                print("BLOCKED: selected entry is not an eligible PENDING without a saved result", file=sys.stderr)
                return 4
            entry_id = candidates[0]
            state["worker_test_sample_ids"] = [entry_id]
            state["worker_codex_vision_test"] = "RUNNING"
            save_progress(state)
            try:
                ordinal = len(set(state["persisted_ids"]) | set(state["review_required_ids"])) + 1
                process_one(state, entry_id, ordinal, force_vision=True, preserve_evidence=True)
            except WorkerStop as exc:
                state["worker_codex_vision_test"] = "FAIL"
                save_progress(state)
                log(f"WORKER_RECOVERY_TEST=FAIL reason={type(exc).__name__}: {str(exc)[:220]}")
                return 5
            valid = evaluate_test_samples([entry_id])
            post = exclusive_partition(state)
            valid = valid and sum(len(post[key]) for key in ("persisted", "pending", "processing", "review_required", "failed")) == 1074
            state["worker_codex_vision_test"] = "PASS" if valid else "FAIL"
            save_progress(state)
            log(f"WORKER_RECOVERY_TEST={'PASS' if valid else 'FAIL'} entry={entry_id} method={VISUAL_METHOD} image_attached=true accounted={sum(len(post[key]) for key in ('persisted','pending','processing','review_required','failed'))}/1074")
            return 0 if valid else 5
        if args.test and len(initial_pending) < 3:
            print("BLOCKED: menos de 3 pendentes para o teste", file=sys.stderr)
            return 4
        if args.test:
            ids = initial_pending[:3]
            state["worker_test_sample_ids"] = ids
            state["worker_codex_vision_test"] = "RUNNING"
            save_progress(state)
            try:
                for entry_id in ids:
                    ordinal = len(set(state["persisted_ids"]) | set(state["review_required_ids"])) + 1
                    process_one(state, entry_id, ordinal, force_vision=True, preserve_evidence=True)
            except WorkerStop as exc:
                state["worker_codex_vision_test"] = "FAIL"
                save_progress(state)
                log(f"WORKER_CODEX_VISION_TEST=FAIL reason={type(exc).__name__}: {str(exc)[:220]}")
                return 5
            valid = evaluate_test_samples(ids)
            state["worker_codex_vision_test"] = "PASS" if valid else "FAIL"
            save_progress(state)
            log(f"WORKER_CODEX_VISION_TEST={'PASS' if valid else 'FAIL'} sample={','.join(ids)} method={VISUAL_METHOD}")
            if not valid:
                return 5
            return 0
        processed = 0
        while state["pending_ids"] and (limit is None or processed < limit):
            entry_id = state["pending_ids"][0]
            state["last_entry_id"] = entry_id
            ordinal = len(set(state["persisted_ids"]) | set(state["review_required_ids"])) + 1
            try:
                process_one(state, entry_id, ordinal)
            except WorkerStop as exc:
                log(f"worker stopped cleanly with pending preserved: {str(exc)[:240]}")
                return 0
            processed += 1
        if not args.max_items:
            try:
                finalize_run(state)
            except WorkerStop as exc:
                log(f"FINAL_VALIDATION=BLOCKED reason={str(exc)[:240]}")
        log(f"worker stopped completed={len(state['persisted_ids']) + len(state['review_required_ids'])} pending={len(state['pending_ids'])} failed={len(state['failed_ids'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
