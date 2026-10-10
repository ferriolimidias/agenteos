import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.protected_baselines import validate

REPO_ROOT = Path(__file__).resolve().parents[2]

BASE_ROUTES = [
    ("/api/empresas/{empresa_id}/conexoes/meta/config", ["GET"]),
    ("/api/empresas/{empresa_id}/conexoes/meta/status", ["GET"]),
    ("/api/empresas/{empresa_id}/conexoes/meta/session", ["POST"]),
    ("/api/empresas/{empresa_id}/conexoes/meta/complete", ["POST"]),
    ("/api/empresas/{empresa_id}/conexoes/meta/assets", ["GET"]),
    ("/api/empresas/{empresa_id}/conexoes/meta/select-number", ["POST"]),
    ("/api/empresas/{empresa_id}/conexoes/{conexao_id}/validate", ["POST"]),
    ("/api/empresas/{empresa_id}/conexoes/{conexao_id}/sync", ["POST"]),
    ("/api/empresas/{empresa_id}/conexoes/{conexao_id}/disconnect", ["POST"]),
    ("/api/empresas/{empresa_id}/whatsapp/status", ["GET"]),
    ("/api/empresas/{empresa_id}/whatsapp/conectar", ["POST"]),
    ("/api/empresas/{empresa_id}/whatsapp/qrcode", ["GET"]),
    ("/api/empresas/{empresa_id}/whatsapp/desconectar", ["POST"]),
    ("/api/empresas/{empresa_id}/instagram/connections", ["GET"]),
    ("/api/empresas/{empresa_id}/instagram/connection", ["POST"]),
    ("/api/empresas/{empresa_id}/instagram/connection/{connection_id}/reconfigure", ["POST"]),
    ("/api/empresas/{empresa_id}/instagram/connection/{connection_id}/revalidate", ["POST"]),
    ("/api/empresas/{empresa_id}/instagram/connection/{connection_id}", ["DELETE"]),
    ("/api/empresas/{empresa_id}/instagram/media", ["GET"]),
    ("/api/empresas/{empresa_id}/instagram/automations", ["GET", "POST"]),
    ("/api/empresas/{empresa_id}/instagram/automations/{automation_id}", ["PUT", "DELETE"]),
    ("/api/empresas/{empresa_id}/instagram/history", ["GET"]),
    ("/api/webhooks/meta/instagram", ["GET", "POST"]),
    ("/api/webhook/{empresa_id}/meta", ["POST"]),
    ("/api/webhook/meta", ["GET", "POST"]),
    ("/api/empresas/{empresa_id}/inbox", ["GET"]),
    ("/api/empresas/{empresa_id}/inbox/conversas", ["GET"]),
    ("/api/empresas/{empresa_id}/inbox/conversas/{conversation_id}/mensagens", ["GET"]),
    ("/api/empresas/{empresa_id}/inbox/{telefone}", ["GET"]),
    ("/api/empresas/{empresa_id}/inbox/{telefone}/send", ["POST"]),
    ("/api/empresas/{empresa_id}/inbox/{telefone}/mensagens", ["POST"]),
    ("/api/empresas/{empresa_id}/inbox/{telefone}/send_media", ["POST"]),
    ("/api/empresas/{empresa_id}/inbox/{telefone}/ai-state", ["GET"]),
    ("/api/empresas/{empresa_id}/inbox/{telefone}/pausar_bot", ["POST"]),
    ("/api/empresas/{empresa_id}/inbox/{telefone}/reativar_bot", ["POST"]),
    ("/api/empresas/{empresa_id}/traffic/meta/config", ["GET", "PUT"]),
    ("/api/empresas/{empresa_id}/traffic/meta/credentials/status", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/me", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/permissions", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/adaccounts", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/adaccounts/{ad_account_id}", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/pages", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/campaigns", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/adsets", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/ads", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/creatives/{creative_id}", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/campaigns/{campaign_id}/inspect", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/insights", ["GET"]),
    ("/api/empresas/{empresa_id}/traffic/meta/campaign-adsets", ["POST"]),
]


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(value).lstrip(), encoding="utf-8")


class ProtectedBaselineGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.baseline = REPO_ROOT / "protected_baselines" / "baselines.json"
        self.route_map = self.root / "routes.json"
        self.write_route_map(BASE_ROUTES)
        self.write_minimal_tree(include_instagram=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_route_map(self, routes: list[tuple[str, list[str]]], path: Path | None = None) -> Path:
        target = path or self.route_map
        target.write_text(
            json.dumps({"routes": [{"path": route, "methods": methods} for route, methods in routes]}),
            encoding="utf-8",
        )
        return target

    def args(self, **overrides):
        values = {
            "workspace_root": str(self.root),
            "baselines": str(self.baseline),
            "workspace_route_map": str(self.route_map),
            "candidate_route_map": "",
            "candidate_root": "",
            "candidate_image": "",
            "require_candidate": False,
            "image_timeout": 10,
        }
        values.update(overrides)
        return type("Args", (), values)()

    def write_minimal_tree(self, include_instagram: bool) -> None:
        write(self.root / "frontend/src/pages/tenant/Conexoes.jsx", '"/whatsapp/status";')
        write(
            self.root / "frontend/src/pages/tenant/Instagram.jsx",
            '''
            function normalizeConnections() {}
            normalizeApiError(error, "Não foi possível carregar as conexões.");
            "instagram/connection/${editingConnectionId}/reconfigure";
            "Instagram ID";
            "Informe o Instagram ID utilizado com o seu Instagram User Access Token.";
            "connection_id: selectedConnectionId";
            "automationForm.privateButtons.length >= 3";
            "isAllowedButtonUrl";
            "postback";
            ''',
        )
        write(
            self.root / "frontend/src/pages/tenant/Inbox.jsx",
            '"/inbox"; chat-conversation-header MessageList <textarea',
        )
        write(
            self.root / "frontend/src/pages/super-admin/EmpresaDetalhes.jsx",
            'conversion_monitor_prompt; Monitor de conversão;',
        )
        write(
            self.root / "frontend/src/pages/tenant/TrafficMeta.jsx",
            '''
            "/traffic/meta/config";
            "ID da conta de anúncios Meta";
            "Validar conta";
            "Nova campanha";
            "Página Meta";
            "Conectado automaticamente pelo WhatsApp Oficial";
            response.data?.success;
            setCreatedStructure(null);
            A campanha não foi criada;
            ''',
        )
        write(
            self.root / "db/models.py",
            '''
            class InstagramCommentAutomation: pass
            connection_id = Column(UUID(as_uuid=True))
            private_reply_config = Column(JSONB)
            class TrafficMetaConfig: pass
            class TrafficMetaCampaignCreation: pass
            fk_traffic_meta_config_empresa_whatsapp_connection
            uq_traffic_meta_configs_ad_account_id
            uq_traffic_meta_campaign_creations_idempotency
            ''',
        )
        write(self.root / "db/migrations/2026_08_11_traffic_meta_mvp_foundation.sql", "CREATE TABLE traffic_meta_configs();")
        write(self.root / "db/migrations/2026_08_11_traffic_meta_campaign_adset_write.sql", "CREATE TABLE traffic_meta_campaign_creations();")
        write(self.root / "db/migrations/2026_07_23_instagram_comment_direct_mvp.sql", "CREATE TABLE instagram_comment_automations();")
        write(self.root / "db/migrations/2026_07_25_instagram_tenant_isolation.sql", "empresa_id connection_id")
        write(self.root / "db/migrations/2026_08_06_instagram_private_reply_button_template.sql", "private_reply_config JSONB")
        write(self.root / "app/services/whatsapp_operational_status.py", "resolve_whatsapp_operational_status")
        write(
            self.root / "app/api/routers/conexoes.py",
            '''
            Conexao.empresa_id == empresa_uuid
            resolve_whatsapp_operational_status
            ''',
        )
        write(self.root / "app/api/routers/webhook.py", '@router.post("/{empresa_id}/meta")')
        write(self.root / "app/api/routers/meta_webhook.py", "Meta Webhook")
        write(self.root / "app/api/routers/inbox.py", "empresa_id")
        write(
            self.root / "app/api/routers/traffic_meta.py",
            '''
            require_tenant_access
            TrafficMetaConfig.empresa_id == empresa_id
            Conexao.empresa_id == empresa_id
            whatsapp_connection_id
            ad_account_validation_id
            await client.validate_ad_account(normalized_ad_account_id)
            _ensure_ad_account_binding_admin
            ctwa_structure_inspector
            MetaMarketingCapability.ADS_MANAGEMENT
            await client.create_campaign(config.ad_account_id, campaign_payload)
            await client.create_adset(config.ad_account_id, adset_payload)
            _ensure_meta_paused_read
            _append_write_audit
            graph_error_type
            graph_error_blame_field_specs
            campaign_http_form
            adset_http_form
            "success": True
            raise HTTPException(status_code=502
            _single_official_whatsapp_for_write
            _validate_page_access
            ''',
        )
        write(
            self.root / "app/services/meta/marketing_credentials.py",
            '''
            class MetaMarketingCapability: pass
            EMBEDDED_SIGNUP_TENANT
            META_MARKETING_ADMIN_ACCESS_TOKEN
            tenant_allowlist_required
            ''',
        )
        write(
            self.root / "app/services/meta/marketing_api.py",
            '''
            META_MARKETING_API_READ_ONLY
            META_MARKETING_API_WRITE_BLOCKED
            def graph_post_data
            async def create_campaign
            async def create_adset
            async def create_creative
            async def create_ad
            adcreatives
            ''',
        )
        write(
            self.root / "app/services/meta/traffic_campaign_builder.py",
            '''
            CAMPAIGN_OBJECTIVE = "OUTCOME_LEADS"
            CAMPAIGN_BUYING_TYPE = "AUCTION"
            ADSET_OPTIMIZATION_GOAL = "CONVERSATIONS"
            ADSET_BILLING_EVENT = "IMPRESSIONS"
            ADSET_DESTINATION_TYPE = "WHATSAPP"
            FORCED_PAUSED_STATUS = "PAUSED"
            whatsapp_phone_number
            targeting_automation
            ''',
        )
        write(self.root / "app/services/meta/conversion_credentials.py", "MetaCredentialResolver")
        write(
            self.root / "app/services/meta/settings.py",
            '''
            DEFAULT_MARKETING_GRAPH_API_VERSION = "v26.0"
            META_MARKETING_WRITE_ENABLED
            META_MARKETING_CAMPAIGN_CREATE_ENABLED
            META_MARKETING_ADSET_CREATE_ENABLED
            META_MARKETING_CREATIVE_CREATE_ENABLED
            META_MARKETING_AD_CREATE_ENABLED
            ''',
        )
        write(
            self.root / "tests/services/test_traffic_meta_foundation.py",
            '''
            test_admin_fallback_is_global_for_new_tenants_and_assets
            test_campaign_and_adset_create_are_flag_gated_and_force_paused
            test_campaign_and_adset_posts_encode_graph_complex_fields
            test_adset_payload_contract_uses_ctwa_real_contract
            ''',
        )
        if include_instagram:
            write(
                self.root / "app/api/routers/instagram_simple.py",
                '''
                instagram_id: str | None
                identification = await identify_connection(
                identified=identification
                ''',
            )
            write(
                self.root / "app/services/instagram_simple.py",
                '''
                INSTAGRAM_USER_IDENTIFIER_LABEL = "Instagram ID"
                GRAPH_BASE_URL = f"https://graph.instagram.com/{GRAPH_VERSION}"
                identify_connection(professional_account_id=requested_id
                "operational": operational
                public_reply
                recent_public_replies
                private_reply
                error_subcode
                fbtrace_id
                Conexao.empresa_id == empresa
                empresa_id=empresa
                Conexao.id == connection_id
                "connections"
                InstagramCommentAutomation.empresa_id == empresa
                InstagramCommentAutomation.connection_id == connection_id
                ''',
            )

    def test_baseline_intact_passes(self):
        code, output = validate(self.args())
        self.assertEqual(code, 0, output)
        self.assertIn("PROTECTED_BASELINES_PASS", output)

    def test_required_instagram_route_absent_fails(self):
        routes = [item for item in BASE_ROUTES if item[0] != "/api/empresas/{empresa_id}/instagram/connections"]
        self.write_route_map(routes)
        code, output = validate(self.args())
        self.assertNotEqual(code, 0)
        self.assertIn("module=instagram_connections", output)
        self.assertIn("contract=required_route_missing", output)

    def test_router_not_registered_fails_static_route_scan(self):
        self.route_map.unlink()
        write(
            self.root / "app/api/main.py",
            '''
            from app.api.routers import instagram_simple
            # router exists, but include_router is missing
            ''',
        )
        write(
            self.root / "app/api/routers/instagram_simple.py",
            '''
            router = APIRouter(prefix="/api/empresas/{empresa_id}/instagram")
            @router.get("/connections")
            async def connections(): pass
            ''',
        )
        code, output = validate(self.args(workspace_route_map=""))
        self.assertNotEqual(code, 0)
        self.assertIn("contract=required_route_missing", output)

    def test_whatsapp_contract_absent_fails(self):
        routes = [item for item in BASE_ROUTES if item[0] != "/api/empresas/{empresa_id}/whatsapp/status"]
        self.write_route_map(routes)
        code, output = validate(self.args())
        self.assertNotEqual(code, 0)
        self.assertIn("module=whatsapp_meta_official", output)

    def test_candidate_runtime_missing_workspace_route_blocks_deploy(self):
        candidate = self.root / "candidate_routes.json"
        routes = [item for item in BASE_ROUTES if item[0] != "/api/empresas/{empresa_id}/instagram/connections"]
        self.write_route_map(routes, candidate)
        code, output = validate(self.args(candidate_route_map=str(candidate), require_candidate=True))
        self.assertNotEqual(code, 0)
        self.assertIn("source=candidate", output)
        self.assertIn("deployment_allowed=false", output)


if __name__ == "__main__":
    unittest.main()
