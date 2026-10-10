from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def assert_contains(path: str, text: str) -> None:
    content = (ROOT / path).read_text(encoding="utf-8")
    if text not in content:
        raise AssertionError(f"{path} nao contem {text!r}")


def main() -> None:
    assert_contains("db/migrations/2026_07_21_publicos_followups_templates.sql", "crm_tag_events")
    assert_contains("db/migrations/2026_07_21_publicos_followups_templates.sql", "contact_import_batches")
    assert_contains("db/migrations/2026_07_21_publicos_followups_templates.sql", "saved_audiences")
    assert_contains("db/migrations/2026_07_21_publicos_followups_templates.sql", "followup_sequence_steps")
    assert_contains("app/api/routers/campaign_central.py", "/audiences/tags")
    assert_contains("app/api/routers/campaign_central.py", "/audiences/imports")
    assert_contains("app/services/campaign_audience_service.py", "csv.Sniffer")
    assert_contains("app/services/campaign_audience_service.py", "CSV_INJECTION_RE")
    assert_contains("frontend/src/pages/tenant/Campanhas.jsx", "Listas por tags")
    assert_contains("frontend/src/pages/tenant/Campanhas.jsx", "Adicionar variável")
    assert_contains("frontend/src/pages/tenant/Campanhas.jsx", "deleteVariable")
    assert_contains("frontend/src/pages/tenant/TenantFollowUps.jsx", "Retomar conversa parada")
    assert_contains("frontend/src/pages/tenant/TenantFollowUps.jsx", "Envio programado / convite")
    assert_contains("frontend/src/pages/tenant/TenantFollowUps.jsx", "Não enviar quando")
    assert_contains("frontend/src/pages/tenant/TenantFollowUps.jsx", "MetaTemplateSelector")
    assert_contains("app/services/followup_service.py", "processar_followups_pendentes")
    assert_contains("app/services/followup_service.py", "followup_engine")
    print("OK publicos_followups_templates verifier")


if __name__ == "__main__":
    main()
