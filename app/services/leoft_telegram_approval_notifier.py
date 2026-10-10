"""Notificação LeoFT segura: dry-run até flag explícita e configuração válida."""
from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen


class TelegramTrainingApprovalNotifier:
    def send_training_for_approval(self, plan: dict[str, Any], context: dict[str, Any], *, send_real: bool = False) -> dict[str, Any]:
        message = _build_message(plan, context)
        chat_id = _telegram_chat_id()
        token = _telegram_bot_token()
        approval_url = str(context.get("approval_url") or "").strip()
        app_url = str(context.get("app_access_url") or context.get("app_url") or "").strip()
        reply_markup = _reply_markup(approval_url=approval_url, app_url=app_url)
        if send_real:
            config = telegram_config_status()
            if not config["valid"]:
                return {
                    "mode": "real",
                    "status": "failed",
                    "sent": False,
                    "message_contains_test_marker": _message_contains_test_marker(message),
                    "chat_id_masked": _mask_chat_id(chat_id) if chat_id else "not_configured",
                    "approval_url_masked": _mask_url(approval_url),
                    "app_access_url_masked": _mask_url(app_url),
                    "error": "telegram_config_invalid",
                    "config": config,
                    "created_at": datetime.utcnow().isoformat(),
                }
            try:
                body = {"chat_id": chat_id, "text": message, "disable_web_page_preview": True}
                if reply_markup:
                    body["reply_markup"] = reply_markup
                request = Request(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=10) as response:  # nosec B310: only explicit operator flag reaches this branch
                    sent = 200 <= response.status < 300
                return {
                    "mode": "real",
                    "status": "sent" if sent else "failed",
                    "sent": sent,
                    "message_contains_test_marker": _message_contains_test_marker(message),
                    "chat_id_masked": _mask_chat_id(chat_id),
                    "approval_url_masked": _mask_url(approval_url),
                    "app_access_url_masked": _mask_url(app_url),
                    "error": None if sent else "telegram_http_error",
                    "created_at": datetime.utcnow().isoformat(),
                }
            except (URLError, TimeoutError, OSError):
                return {
                    "mode": "real",
                    "status": "failed",
                    "sent": False,
                    "message_contains_test_marker": _message_contains_test_marker(message),
                    "chat_id_masked": _mask_chat_id(chat_id),
                    "approval_url_masked": _mask_url(approval_url),
                    "app_access_url_masked": _mask_url(app_url),
                    "error": "telegram_transport_error",
                    "created_at": datetime.utcnow().isoformat(),
                }
        return {
            "mode": "dry_run",
            "status": "dry_run",
            "sent": False,
            "message_contains_test_marker": _message_contains_test_marker(message),
            "chat_id_masked": _mask_chat_id(chat_id) if chat_id else "not_configured",
            "approval_url_masked": _mask_url(approval_url),
            "app_access_url_masked": _mask_url(app_url),
            "error": None,
            "created_at": datetime.utcnow().isoformat(),
        }


def telegram_config_status() -> dict[str, Any]:
    bot_token = _telegram_bot_token()
    chat_id = _telegram_chat_id()
    approval_base_url = str(os.getenv("LEOFT_APPROVAL_BASE_URL") or "").strip()
    bot_token_placeholder = _has_placeholder(bot_token)
    chat_id_placeholder = _has_placeholder(chat_id)
    approval_base_url_placeholder = _has_placeholder(approval_base_url)
    approval_base_url_valid = _valid_http_url(approval_base_url) and not approval_base_url_placeholder
    placeholder_detected = bot_token_placeholder or chat_id_placeholder or approval_base_url_placeholder
    real_enabled = str(os.getenv("LEOFT_TELEGRAM_REAL_ENABLED") or "").strip().lower() == "true"
    return {
        "bot_token_present": bool(bot_token),
        "bot_token_valid": bool(bot_token) and not bot_token_placeholder,
        "treinos_chat_id_present": bool(chat_id),
        "treinos_chat_id_valid": bool(chat_id) and not chat_id_placeholder,
        "real_enabled": real_enabled,
        "approval_base_url_present": bool(approval_base_url),
        "approval_base_url_valid": approval_base_url_valid,
        "placeholder_detected": placeholder_detected,
        "valid": real_enabled
        and bool(bot_token)
        and not bot_token_placeholder
        and bool(chat_id)
        and not chat_id_placeholder
        and approval_base_url_valid,
    }


def _telegram_bot_token() -> str:
    return str(os.getenv("LEOFT_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


def _telegram_chat_id() -> str:
    return str(
        os.getenv("LEOFT_TELEGRAM_TREINOS_CHAT_ID")
        or os.getenv("TELEGRAM_TREINOS_CHAT_ID")
        or os.getenv("LEOFT_TELEGRAM_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
        or ""
    ).strip()


def _build_message(plan: dict[str, Any], context: dict[str, Any]) -> str:
    months = plan.get("months") if isinstance(plan.get("months"), list) else []
    first_month = months[0] if months and isinstance(months[0], dict) else {}
    first_week = (first_month.get("weeks") or [{}])[0] if isinstance(first_month.get("weeks"), list) else {}
    first_workout = (first_week.get("workouts") or [{}])[0] if isinstance(first_week.get("workouts"), list) else {}
    first_exercise = (first_workout.get("exercises") or [{}])[0] if isinstance(first_workout.get("exercises"), list) else {}
    lines = [
        "TESTE E2E - LeoFT",
        "",
        f"Aluno fictício: {context.get('student_name') or plan.get('studentName') or 'Aluno Teste LeoFT'}",
        f"Objetivo: {context.get('main_goal') or 'Voltar a treinar com constância'}",
        f"Tipo: {context.get('training_type') or first_workout.get('type') or 'Musculação + corrida'}",
        f"Duração: {plan.get('durationMonths') or 3} meses",
        f"Plan ID: {context.get('plan_id') or plan.get('planId') or '-'}",
        "Status: pending_approval",
        "",
        "Resumo do plano:",
        f"- Mês 1: {first_month.get('title') or 'Base'}",
        "- Mês 2: Progressão",
        "- Mês 3: Consolidação",
        "",
        "Amostra:",
        f"- {first_exercise.get('name') or 'Exercício principal'}: {first_exercise.get('sets') or ''}x{first_exercise.get('reps') or ''}".strip(),
        "",
        "Aprovação real de TESTE: use o botão abaixo para liberar o app do aluno fictício.",
    ]
    return "\n".join(lines)


def _message_contains_test_marker(message: str) -> bool:
    return "TESTE" in str(message or "")


def _reply_markup(*, approval_url: str, app_url: str) -> dict[str, Any] | None:
    buttons = []
    if _valid_http_url(approval_url):
        buttons.append({"text": "Aprovar treino", "url": approval_url})
    if _valid_http_url(app_url):
        buttons.append({"text": "Ver app do aluno", "url": app_url})
    if not buttons:
        return None
    return {"inline_keyboard": [[button] for button in buttons]}


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _has_placeholder(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    upper = text.upper()
    return any(marker in upper for marker in ("COLE_AQUI", "CHANGE_ME", "TODO", "PLACEHOLDER", "VAZIO")) or "example" in text.lower()


def _mask_chat_id(value: str) -> str:
    text = str(value or "")
    if len(text) <= 4:
        return "***"
    return text[:3] + "..." + text[-2:]


def _mask_url(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if "token=" in text:
        prefix, token = text.split("token=", 1)
        if len(token) > 12:
            token = token[:4] + "..." + token[-4:]
        else:
            token = "***"
        return prefix + "token=" + token
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/..."
    return "***"
