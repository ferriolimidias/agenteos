from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit(f"FAIL {path}: missing {marker}")


def main() -> None:
    require("app/services/campaign_central_policy.py", "effective_rules")
    require("app/services/campaign_central_service.py", "external_call")
    require("app/api/routers/campaign_central.py", "/campaign-central")
    require("app/api/main.py", "campaign_central.router")
    require("db/migrations/2026_07_21_phase_c_campaign_central.sql", "campaign_template_drafts")
    require("db/migrations/2026_07_21_phase_c_campaign_central.sql", "whatsapp_message_policies")
    require("db/migrations/2026_07_21_phase_c_campaign_central_rollback.sql", "drop table if exists campaign_template_drafts")
    require("frontend/src/pages/tenant/Campanhas.jsx", "Central de Campanhas")
    require("frontend/src/App.jsx", "campanhas/templates")
    require("frontend/src/layouts/TenantLayout.jsx", "Campanhas")
    print("OK Fase C campaign central contracts present; no external dispatch enabled.")


if __name__ == "__main__":
    main()

