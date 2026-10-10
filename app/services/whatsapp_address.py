from __future__ import annotations

import re
from dataclasses import dataclass


BR_COUNTRY_CODE = "55"


@dataclass(frozen=True)
class WhatsAppAddress:
    raw_sanitized: str
    digits: str
    e164: str | None
    country_code: str | None
    national_number: str | None
    valid: bool
    country: str | None
    masked_display: str | None
    normalization_reason: str


@dataclass(frozen=True)
class WhatsAppAddressComparison:
    result: str
    left: WhatsAppAddress
    right: WhatsAppAddress
    match: bool


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _mask(digits: str) -> str | None:
    if not digits:
        return None
    if len(digits) <= 6:
        return "***"
    return f"{digits[:4]}***{digits[-4:]}"


def normalize_whatsapp_address(raw_value: str | None, *, default_country: str | None = "BR") -> WhatsAppAddress:
    raw_sanitized = str(raw_value or "").strip()
    digits = _digits(raw_sanitized)
    default = str(default_country or "").strip().upper()
    has_explicit_plus = raw_sanitized.startswith("+")
    reason = "digits_only"

    if (
        default in {"BR", "55", "BRA"}
        and digits
        and not has_explicit_plus
        and not digits.startswith(BR_COUNTRY_CODE)
        and len(digits) in {10, 11}
    ):
        digits = f"{BR_COUNTRY_CODE}{digits}"
        reason = "default_country_br_applied"

    country_code: str | None = None
    national_number: str | None = None
    country: str | None = None
    if digits.startswith(BR_COUNTRY_CODE) and len(digits) in {12, 13}:
        country_code = BR_COUNTRY_CODE
        national_number = digits[2:]
        country = "BR"

    valid = 10 <= len(digits) <= 15
    if country_code == BR_COUNTRY_CODE:
        valid = len(digits) in {12, 13}

    return WhatsAppAddress(
        raw_sanitized=raw_sanitized,
        digits=digits,
        e164=f"+{digits}" if valid and digits else None,
        country_code=country_code,
        national_number=national_number,
        valid=valid,
        country=country,
        masked_display=_mask(digits),
        normalization_reason=reason,
    )


def _br_ninth_digit_equivalent(left: str, right: str) -> bool:
    if not (left.startswith(BR_COUNTRY_CODE) and right.startswith(BR_COUNTRY_CODE)):
        return False
    if {len(left), len(right)} != {12, 13}:
        return False
    longer, shorter = (left, right) if len(left) == 13 else (right, left)
    ddd_longer = longer[2:4]
    ddd_shorter = shorter[2:4]
    if ddd_longer != ddd_shorter:
        return False
    if longer[4] != "9":
        return False
    return f"{longer[:4]}{longer[5:]}" == shorter


def compare_whatsapp_addresses(
    authorized_number: str | None,
    inbound_wa_id: str | None,
    *,
    default_country: str | None = "BR",
) -> WhatsAppAddressComparison:
    left = normalize_whatsapp_address(authorized_number, default_country=default_country)
    right = normalize_whatsapp_address(inbound_wa_id, default_country=default_country)

    if not left.digits or not right.digits or not left.valid or not right.valid:
        return WhatsAppAddressComparison("invalid_input", left, right, False)
    if _digits(authorized_number) == _digits(inbound_wa_id):
        return WhatsAppAddressComparison("exact_match", left, right, True)
    if left.digits == right.digits:
        return WhatsAppAddressComparison("normalized_match", left, right, True)
    if _br_ninth_digit_equivalent(left.digits, right.digits):
        return WhatsAppAddressComparison("br_mobile_ninth_digit_equivalent", left, right, True)
    return WhatsAppAddressComparison("no_match", left, right, False)


def mask_whatsapp_address(value: str | None, *, default_country: str | None = "BR") -> str | None:
    return normalize_whatsapp_address(value, default_country=default_country).masked_display
