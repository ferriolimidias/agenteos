from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FLOW = (ROOT / "apps/anamnese-leonardo/src/features/anamnese/AnamneseFlow.tsx").read_text()
TYPES = (ROOT / "apps/anamnese-leonardo/src/features/anamnese/lib/types.ts").read_text()
ROUTER = (ROOT / "app/api/routers/public_anamnese.py").read_text()


def test_anamnese_only_asks_name_for_contact_identity():
    assert 'title="Qual é teu nome?"' in FLOW
    assert "whatsapp" not in FLOW
    assert "email" not in FLOW
    assert "whatsapp" not in TYPES
    assert "email" not in TYPES


def test_server_owns_phone_and_rejects_missing_linked_phone():
    assert "canonical_phone = _lead_phone(lead)" in ROUTER
    assert 'raw_personal.pop("whatsapp", None)' in ROUTER
    assert 'raw_personal.pop("email", None)' in ROUTER
    assert "Vínculo de telefone do lead indisponível." in ROUTER


def test_public_anamnese_uses_canonical_record_with_legacy_fallback():
    assert "link_slug = str(validation.link_slug or slug).strip()" in ROUTER
    assert "_get_anamnese_record(anamneses, link_slug) or _get_anamnese_record(anamneses, slug)" in ROUTER
    assert "anamneses[link_slug] = current" in ROUTER
    assert "anamneses[slug] = current" not in ROUTER


def test_session_refresh_reads_checkout_from_canonical_or_legacy_record():
    session_block = ROUTER.split("@router.post", 1)[0]
    assert "_get_anamnese_record(anamneses, validation.link_slug or validation.slug)" in session_block
    assert "_get_anamnese_record(\n        anamneses,\n        validation.slug,\n    )" in session_block
    assert '"checkout": _safe_checkout(anamnese)' in session_block


def test_checkout_contract_does_not_require_email():
    service = (ROOT / "app/services/leoft_checkout_service.py").read_text()
    assert "if not access_token" in service
    assert "email" in service  # optional payer field only
    assert "email_required" not in service
