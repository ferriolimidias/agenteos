from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Iterable, Literal


FOLLOWUP_MODE_CONVERSATION_RESUME = "conversation_resume"
FOLLOWUP_MODE_SCHEDULED_MESSAGE = "scheduled_message"

FOLLOWUP_MODES = {
    FOLLOWUP_MODE_CONVERSATION_RESUME,
    FOLLOWUP_MODE_SCHEDULED_MESSAGE,
}

FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL = "ai_contextual"
FOLLOWUP_MESSAGE_MODE_FIXED_TEXT = "fixed_text"
FOLLOWUP_MESSAGE_MODE_META_TEMPLATE = "meta_template"

FOLLOWUP_MESSAGE_MODES = {
    FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL,
    FOLLOWUP_MESSAGE_MODE_FIXED_TEXT,
    FOLLOWUP_MESSAGE_MODE_META_TEMPLATE,
}

FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE = "last_agent_message"
FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE = "first_inbound_message"
FOLLOWUP_ANCHOR_AD_ENTRY = "ad_entry"
FOLLOWUP_ANCHOR_TAG_APPLIED = "tag_applied"
FOLLOWUP_ANCHOR_CONTACT_CREATED = "contact_created"
FOLLOWUP_ANCHOR_FIXED_DATETIME = "fixed_datetime"
FOLLOWUP_ANCHOR_OTHER_EVENT = "other_event"

FOLLOWUP_ANCHORS = {
    FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE,
    FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE,
    FOLLOWUP_ANCHOR_AD_ENTRY,
    FOLLOWUP_ANCHOR_TAG_APPLIED,
    FOLLOWUP_ANCHOR_CONTACT_CREATED,
    FOLLOWUP_ANCHOR_FIXED_DATETIME,
    FOLLOWUP_ANCHOR_OTHER_EVENT,
}

FOLLOWUP_MODE_LABELS = {
    FOLLOWUP_MODE_CONVERSATION_RESUME: "Retomar conversa parada",
    FOLLOWUP_MODE_SCHEDULED_MESSAGE: "Envio programado / convite",
}

FOLLOWUP_ANCHOR_LABELS = {
    FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE: "Última mensagem do agente",
    FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE: "Primeira mensagem da pessoa",
    FOLLOWUP_ANCHOR_AD_ENTRY: "Entrada pelo anúncio",
    FOLLOWUP_ANCHOR_TAG_APPLIED: "Aplicação de tag",
    FOLLOWUP_ANCHOR_CONTACT_CREATED: "Criação do contato",
    FOLLOWUP_ANCHOR_FIXED_DATETIME: "Data e horário fixos",
    FOLLOWUP_ANCHOR_OTHER_EVENT: "Outro evento",
}

DelayUnit = Literal["minutes", "hours", "days"]


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "sim", "on"}:
        return True
    if text in {"0", "false", "no", "nao", "não", "off"}:
        return False
    return default


def _coerce_str(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_time_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value.strftime("%H:%M")
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = time.fromisoformat(text[:8])
    except ValueError:
        return None
    return parsed.strftime("%H:%M")


def normalize_message_mode(value: Any, *, template_id: Any = None) -> str:
    raw = _coerce_str(value).lower()
    if raw in FOLLOWUP_MESSAGE_MODES:
        return raw
    if raw in {"ai", "ia", "generate_with_ai", "gerar_com_ia"}:
        return FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL
    if raw in {"template", "whatsapp_template", "meta"}:
        return FOLLOWUP_MESSAGE_MODE_META_TEMPLATE
    # Cadastros antigos continuam como texto fixo por padrao, mesmo quando
    # havia template opcional como fallback historico.
    return FOLLOWUP_MESSAGE_MODE_FIXED_TEXT


def normalize_followup_mode(value: Any, *, trigger_tag_id: Any = None, anchor_type: Any = None) -> str:
    raw = _coerce_str(value).lower()
    if raw in FOLLOWUP_MODES:
        return raw

    anchor = _coerce_str(anchor_type).lower()
    if anchor in FOLLOWUP_ANCHORS and anchor not in {FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE}:
        return FOLLOWUP_MODE_SCHEDULED_MESSAGE
    if _coerce_str(trigger_tag_id):
        return FOLLOWUP_MODE_SCHEDULED_MESSAGE
    return FOLLOWUP_MODE_CONVERSATION_RESUME


def normalize_anchor_type(value: Any, *, mode: str | None = None) -> str:
    raw = _coerce_str(value).lower()
    if raw in FOLLOWUP_ANCHORS:
        return raw
    mode_norm = normalize_followup_mode(mode)
    if mode_norm == FOLLOWUP_MODE_CONVERSATION_RESUME:
        return FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE
    return FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE


def normalize_delay_minutes(delay_value: Any, delay_unit: Any, legacy_minutes: Any = None) -> int:
    if delay_value in (None, "") and legacy_minutes not in (None, ""):
        try:
            return max(0, int(float(str(legacy_minutes))))
        except (TypeError, ValueError):
            return 0

    try:
        value = max(0, int(float(str(delay_value))))
    except (TypeError, ValueError):
        value = 0

    unit = _coerce_str(delay_unit, "minutes").lower()
    if unit == "hours":
        return value * 60
    if unit == "days":
        return value * 60 * 24
    return value


def split_delay_minutes(delay_minutes: Any) -> tuple[int, DelayUnit]:
    try:
        total = max(0, int(float(str(delay_minutes))))
    except (TypeError, ValueError):
        total = 0
    if total and total % (60 * 24) == 0:
        return total // (60 * 24), "days"
    if total and total % 60 == 0:
        return total // 60, "hours"
    return total, "minutes"


def parse_tag_id_list(value: Any) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        raw_items: Iterable[Any] = [value] if value not in (None, "") else []
    else:
        raw_items = value
    output: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _coerce_str(item)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _jsonable_variable_mapping(value: Any) -> tuple[dict[str, Any], ...]:
    raw = value or []
    if not isinstance(raw, list):
        return tuple()
    output: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            output.append(dict(item))
    return tuple(output)


@dataclass(frozen=True)
class FollowupRuleSpec:
    mode: str = FOLLOWUP_MODE_CONVERSATION_RESUME
    delay_minutes: int = 0
    anchor_type: str = FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE
    message_content: str = ""
    template_id: str | None = None
    cancel_on_inbound: bool = True
    cancel_on_human: bool = True
    cancel_on_opt_out: bool = True
    cancel_on_event_end: bool = True
    cancel_tag_ids: tuple[str, ...] = field(default_factory=tuple)
    activation_at: datetime | None = None
    campaign_or_event_id: str | None = None
    variable_mapping: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    needs_review: bool = False
    trigger_tag_id: str | None = None
    message_mode: str = FOLLOWUP_MESSAGE_MODE_FIXED_TEXT
    content_type: str = "text"
    media_asset_id: str | None = None
    send_as_voice: bool = False
    audio_caption: str | None = None
    send_window_enabled: bool = False
    send_window_start: str | None = None
    send_window_end: str | None = None
    timezone: str = "America/Sao_Paulo"
    limit_to_free_entry_window: bool = False

    @property
    def mode_label(self) -> str:
        return FOLLOWUP_MODE_LABELS.get(self.mode, self.mode)

    @property
    def anchor_label(self) -> str:
        return FOLLOWUP_ANCHOR_LABELS.get(self.anchor_type, self.anchor_type)

    @property
    def is_conversation_resume(self) -> bool:
        return self.mode == FOLLOWUP_MODE_CONVERSATION_RESUME

    @property
    def is_scheduled_message(self) -> bool:
        return self.mode == FOLLOWUP_MODE_SCHEDULED_MESSAGE

    @classmethod
    def from_payload(cls, payload: Any, *, base: "FollowupRuleSpec" | None = None) -> "FollowupRuleSpec":
        fields_set = set(
            getattr(payload, "model_fields_set", None)
            or getattr(payload, "__pydantic_fields_set__", None)
            or set()
        )

        def _value(name: str, default: Any = None) -> Any:
            if name in fields_set:
                return getattr(payload, name, default)
            if base is not None and hasattr(base, name):
                return getattr(base, name)
            return default

        delay_value = _value("delay_value", None)
        delay_unit = _value("delay_unit", None)
        legacy_minutes = _value("delay_minutes", None)
        if delay_value in (None, ""):
            delay_value = legacy_minutes if legacy_minutes not in (None, "") else _value("tempo_gatilho_minutos", None)

        mode = normalize_followup_mode(
            _value("mode", None),
            trigger_tag_id=_value("trigger_tag_id", None),
            anchor_type=_value("anchor_type", None),
        )
        anchor_type = normalize_anchor_type(_value("anchor_type", None), mode=mode)
        if mode == FOLLOWUP_MODE_CONVERSATION_RESUME:
            anchor_type = FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE
        elif not anchor_type:
            anchor_type = FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE

        message_content = _coerce_str(
            _value("message_content", None)
            or _value("message_free_text", None)
            or _value("objetivo_prompt", None)
        )
        template_id = _coerce_str(_value("template_id", None)) or None
        message_mode = normalize_message_mode(_value("message_mode", None), template_id=template_id)
        activation_at = _parse_datetime(_value("activation_at", None))
        campaign_or_event_id = _coerce_str(_value("campaign_or_event_id", None)) or None
        variable_mapping = _jsonable_variable_mapping(_value("variable_mapping", None))
        cancel_tag_ids = tuple(parse_tag_id_list(_value("cancel_tag_ids", None)))
        cancel_on_inbound = _coerce_bool(_value("cancel_on_inbound", None), default=base.cancel_on_inbound if base is not None else mode == FOLLOWUP_MODE_CONVERSATION_RESUME)
        cancel_on_human = _coerce_bool(_value("cancel_on_human", None), default=base.cancel_on_human if base is not None else True)
        cancel_on_opt_out = _coerce_bool(_value("cancel_on_opt_out", None), default=base.cancel_on_opt_out if base is not None else True)
        cancel_on_event_end = _coerce_bool(_value("cancel_on_event_end", None), default=base.cancel_on_event_end if base is not None else mode == FOLLOWUP_MODE_SCHEDULED_MESSAGE)
        needs_review = _coerce_bool(_value("needs_review", None), default=base.needs_review if base is not None else False)
        trigger_tag_id = _coerce_str(_value("trigger_tag_id", None)) or None
        if not trigger_tag_id and anchor_type == FOLLOWUP_ANCHOR_TAG_APPLIED and campaign_or_event_id:
            trigger_tag_id = campaign_or_event_id

        return cls(
            mode=mode,
            delay_minutes=normalize_delay_minutes(delay_value, delay_unit, legacy_minutes),
            anchor_type=anchor_type,
            message_content=message_content,
            template_id=template_id,
            cancel_on_inbound=cancel_on_inbound,
            cancel_on_human=cancel_on_human,
            cancel_on_opt_out=cancel_on_opt_out,
            cancel_on_event_end=cancel_on_event_end,
            cancel_tag_ids=cancel_tag_ids,
            activation_at=activation_at,
            campaign_or_event_id=campaign_or_event_id,
            variable_mapping=variable_mapping,
            needs_review=needs_review,
            trigger_tag_id=trigger_tag_id,
            message_mode=message_mode,
            content_type=_coerce_str(_value("content_type", None), "text").lower() if _coerce_str(_value("content_type", None), "text").lower() in {"text", "audio", "text_audio"} else "text",
            media_asset_id=_coerce_str(_value("media_asset_id", None)) or None,
            send_as_voice=_coerce_bool(_value("send_as_voice", None), default=False),
            audio_caption=_coerce_str(_value("audio_caption", None)) or None,
            send_window_enabled=_coerce_bool(_value("send_window_enabled", None), default=base.send_window_enabled if base is not None else False),
            send_window_start=_parse_time_text(_value("send_window_start", None)) or (base.send_window_start if base is not None else None),
            send_window_end=_parse_time_text(_value("send_window_end", None)) or (base.send_window_end if base is not None else None),
            timezone=_coerce_str(_value("timezone", None), base.timezone if base is not None else "America/Sao_Paulo"),
            limit_to_free_entry_window=_coerce_bool(_value("limit_to_free_entry_window", None), default=base.limit_to_free_entry_window if base is not None else False),
        )

    @classmethod
    def from_row(cls, item: Any, step: dict[str, Any] | None = None) -> "FollowupRuleSpec":
        step = dict(step or {})
        execute_conditions = step.get("execute_conditions_json") if isinstance(step.get("execute_conditions_json"), dict) else {}
        cancel_conditions = step.get("cancel_conditions_json") if isinstance(step.get("cancel_conditions_json"), dict) else {}
        trigger_tag_id = _coerce_str(step.get("trigger_tag_id") or execute_conditions.get("trigger_tag_id")) or None
        mode = normalize_followup_mode(
            execute_conditions.get("mode"),
            trigger_tag_id=trigger_tag_id,
            anchor_type=execute_conditions.get("anchor_type"),
        )
        anchor_type = normalize_anchor_type(execute_conditions.get("anchor_type"), mode=mode)
        message_content = _coerce_str(step.get("message_free_text") or getattr(item, "objetivo_prompt", None))
        cancel_tag_ids = tuple(parse_tag_id_list(
            cancel_conditions.get("cancel_tag_ids")
            or cancel_conditions.get("tag_ids")
            or cancel_conditions.get("tags")
        ))
        activation_at = _parse_datetime(execute_conditions.get("activation_at"))
        campaign_or_event_id = _coerce_str(
            execute_conditions.get("campaign_or_event_id")
            or execute_conditions.get("event_id")
            or execute_conditions.get("anchor_event_id")
        ) or None
        variable_mapping = _jsonable_variable_mapping(step.get("variable_mapping_json"))
        message_mode = normalize_message_mode(
            step.get("message_mode") or execute_conditions.get("message_mode"),
            template_id=step.get("template_id"),
        )
        needs_review = (
            _coerce_bool(execute_conditions.get("needs_review"), default=False)
            or _coerce_bool(cancel_conditions.get("needs_review"), default=False)
        )

        cancel_on_inbound = _coerce_bool(cancel_conditions.get("cancel_on_inbound"), default=mode == FOLLOWUP_MODE_CONVERSATION_RESUME)
        cancel_on_human = _coerce_bool(cancel_conditions.get("cancel_on_human"), default=True)
        cancel_on_opt_out = _coerce_bool(cancel_conditions.get("cancel_on_opt_out"), default=True)
        cancel_on_event_end = _coerce_bool(cancel_conditions.get("cancel_on_event_end"), default=mode == FOLLOWUP_MODE_SCHEDULED_MESSAGE)
        delay_minutes = step.get("delay_minutes")
        if delay_minutes in (None, ""):
            delay_minutes = getattr(item, "tempo_gatilho_minutos", 0)

        return cls(
            mode=mode,
            delay_minutes=max(0, int(float(str(delay_minutes or 0)))),
            anchor_type=anchor_type,
            message_content=message_content,
            template_id=_coerce_str(step.get("template_id")) or None,
            cancel_on_inbound=cancel_on_inbound,
            cancel_on_human=cancel_on_human,
            cancel_on_opt_out=cancel_on_opt_out,
            cancel_on_event_end=cancel_on_event_end,
            cancel_tag_ids=cancel_tag_ids,
            activation_at=activation_at,
            campaign_or_event_id=campaign_or_event_id,
            variable_mapping=variable_mapping,
            needs_review=needs_review,
            trigger_tag_id=trigger_tag_id,
            message_mode=message_mode,
            content_type=_coerce_str(step.get("content_type"), "text").lower() if _coerce_str(step.get("content_type"), "text").lower() in {"text", "audio", "text_audio"} else "text",
            media_asset_id=_coerce_str(step.get("media_asset_id")) or None,
            send_as_voice=_coerce_bool(step.get("send_as_voice"), default=False),
            audio_caption=_coerce_str(step.get("audio_caption")) or None,
            send_window_enabled=_coerce_bool(step.get("send_window_enabled") if step.get("send_window_enabled") is not None else execute_conditions.get("send_window_enabled"), default=False),
            send_window_start=_parse_time_text(step.get("send_window_start") or execute_conditions.get("send_window_start")),
            send_window_end=_parse_time_text(step.get("send_window_end") or execute_conditions.get("send_window_end")),
            timezone=_coerce_str(step.get("timezone") or execute_conditions.get("timezone"), "America/Sao_Paulo"),
            limit_to_free_entry_window=_coerce_bool(step.get("limit_to_free_entry_window") if step.get("limit_to_free_entry_window") is not None else execute_conditions.get("limit_to_free_entry_window"), default=False),
        )

    def to_storage_payload(self) -> dict[str, Any]:
        delay_value, delay_unit = split_delay_minutes(self.delay_minutes)
        return {
            "delay_minutes": self.delay_minutes,
            "execute_conditions_json": {
                "mode": self.mode,
                "anchor_type": self.anchor_type,
                "activation_at": self.activation_at.isoformat() if self.activation_at else None,
                "campaign_or_event_id": self.campaign_or_event_id,
                "delay_value": delay_value,
                "delay_unit": delay_unit,
                "needs_review": bool(self.needs_review),
                "trigger_tag_id": self.trigger_tag_id,
                "message_mode": self.message_mode,
                "send_window_enabled": bool(self.send_window_enabled),
                "send_window_start": self.send_window_start,
                "send_window_end": self.send_window_end,
                "timezone": self.timezone,
                "limit_to_free_entry_window": bool(self.limit_to_free_entry_window),
            },
            "cancel_conditions_json": {
                "cancel_on_inbound": bool(self.cancel_on_inbound),
                "cancel_on_human": bool(self.cancel_on_human),
                "cancel_on_opt_out": bool(self.cancel_on_opt_out),
                "cancel_on_event_end": bool(self.cancel_on_event_end),
                "cancel_tag_ids": list(self.cancel_tag_ids),
                "needs_review": bool(self.needs_review),
            },
            "message_free_text": self.message_content,
            "template_id": self.template_id,
            "message_mode": self.message_mode,
            "variable_mapping_json": [dict(item) for item in self.variable_mapping],
            "trigger_tag_id": self.trigger_tag_id,
            "post_send_tag_id": None,
            "actions_json": [],
            "content_type": self.content_type,
            "media_asset_id": self.media_asset_id,
            "send_as_voice": bool(self.send_as_voice),
            "audio_caption": self.audio_caption,
            "send_window_enabled": bool(self.send_window_enabled),
            "send_window_start": self.send_window_start,
            "send_window_end": self.send_window_end,
            "timezone": self.timezone,
            "limit_to_free_entry_window": bool(self.limit_to_free_entry_window),
        }

    def to_response_dict(
        self,
        *,
        item: Any,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> dict[str, Any]:
        delay_value, delay_unit = split_delay_minutes(self.delay_minutes)
        return {
            "id": str(getattr(item, "id", "") or ""),
            "empresa_id": str(getattr(item, "empresa_id", "") or ""),
            "nome": _coerce_str(getattr(item, "nome", "")),
            "ativo": bool(getattr(item, "ativo", True)),
            "mode": self.mode,
            "delay_value": delay_value,
            "delay_unit": delay_unit,
            "anchor_type": self.anchor_type,
            "message_content": self.message_content,
            "template_id": self.template_id,
            "cancel_on_inbound": bool(self.cancel_on_inbound),
            "cancel_on_human": bool(self.cancel_on_human),
            "cancel_on_opt_out": bool(self.cancel_on_opt_out),
            "cancel_on_event_end": bool(self.cancel_on_event_end),
            "cancel_tag_ids": list(self.cancel_tag_ids),
            "activation_at": self.activation_at.isoformat() if self.activation_at else None,
            "campaign_or_event_id": self.campaign_or_event_id,
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else created_at.isoformat() if created_at else None,
            "needs_review": bool(self.needs_review),
            "message_mode": self.message_mode,
            "content_type": self.content_type,
            "media_asset_id": self.media_asset_id,
            "send_as_voice": bool(self.send_as_voice),
            "audio_caption": self.audio_caption,
            "send_window_enabled": bool(self.send_window_enabled),
            "send_window_start": self.send_window_start,
            "send_window_end": self.send_window_end,
            "timezone": self.timezone,
            "limit_to_free_entry_window": bool(self.limit_to_free_entry_window),
        }

    def cancel_reasons(self) -> dict[str, bool]:
        return {
            "cancel_on_inbound": bool(self.cancel_on_inbound),
            "cancel_on_human": bool(self.cancel_on_human),
            "cancel_on_opt_out": bool(self.cancel_on_opt_out),
            "cancel_on_event_end": bool(self.cancel_on_event_end),
            "cancel_tag_ids": bool(self.cancel_tag_ids),
        }
