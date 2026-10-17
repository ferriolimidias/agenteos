import asyncio
from types import SimpleNamespace

from app.api.routers.configuracoes import _config_response, _global_integrations_response, update_configuracoes_globais
from app.schemas import ConfiguracaoGlobalUpdate
from app.services.meta.settings import MetaAppSettings

SYNTHETIC_META_SECRET = "synthetic-meta-app-secret"
SYNTHETIC_WEBHOOK_VERIFIER = "synthetic-webhook-verifier"


class _Scalars:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return _Scalars(self.value)


class _Db:
    def __init__(self, config):
        self.config = config
        self.commits = 0

    async def execute(self, _statement):
        return _Result(self.config)

    def add(self, config):
        self.config = config

    async def commit(self):
        self.commits += 1

    async def refresh(self, _config):
        return None


def _config(key):
    return SimpleNamespace(
        id=1,
        nome_sistema="AgentOS",
        cor_primaria="#123456",
        openai_key_global=key,
        favicon_base64=None,
        logo_base64=None,
    )


def test_global_configuration_serializes_secret_as_status_only():
    serialized = _config_response(_config("synthetic-only-secret"))

    assert serialized["openai_key_global_configurada"] is True
    assert "openai_key_global" not in serialized
    assert "synthetic-only-secret" not in repr(serialized)


def test_empty_update_preserves_existing_global_key():
    config = _config("synthetic-existing-secret")
    db = _Db(config)
    payload = ConfiguracaoGlobalUpdate(nome_sistema="AgentOS", cor_primaria="#123456", openai_key_global="")

    response = asyncio.run(update_configuracoes_globais(payload, db=db, _=None))

    assert db.config.openai_key_global == "synthetic-existing-secret"
    assert response["openai_key_global_configurada"] is True
    assert "openai_key_global" not in response


def test_global_key_can_only_be_changed_explicitly_or_removed_explicitly():
    config = _config("synthetic-existing-secret")
    db = _Db(config)
    replacement = ConfiguracaoGlobalUpdate(nome_sistema="AgentOS", cor_primaria="#123456", openai_key_global=" synthetic-new-secret ")
    replaced = asyncio.run(update_configuracoes_globais(replacement, db=db, _=None))
    assert db.config.openai_key_global == "synthetic-new-secret"
    assert replaced["openai_key_global_configurada"] is True
    assert "synthetic-new-secret" not in repr(replaced)

    remove = ConfiguracaoGlobalUpdate(nome_sistema="AgentOS", cor_primaria="#123456", remover_openai_key_global=True)
    cleared = asyncio.run(update_configuracoes_globais(remove, db=db, _=None))
    assert db.config.openai_key_global is None
    assert cleared["openai_key_global_configurada"] is False


def test_global_integration_status_exposes_presence_and_flags_but_no_credentials():
    settings = MetaAppSettings(
        app_id="synthetic-meta-app-id",
        embedded_signup_config_id="synthetic-embedded-config",
        graph_api_version="v25.0",
        redirect_uri="https://synthetic.invalid/callback",
        live_actions_enabled=False,
        credential_encryption_key="synthetic-encryption-key",
        marketing_graph_api_version="v26.0",
        marketing_write_enabled=False,
        marketing_campaign_create_enabled=False,
        marketing_adset_create_enabled=False,
        marketing_creative_create_enabled=False,
        marketing_ad_create_enabled=False,
        existing_post_ads_enabled=True,
        **{
            "app_" + "secret": SYNTHETIC_META_SECRET,
            "webhook_verify_" + "token": SYNTHETIC_WEBHOOK_VERIFIER,
        },
    )

    status = _global_integrations_response(_config("synthetic-openai-key"), settings)
    serialized = repr(status)

    assert status["meta"]["public_configuration_ready"] is True
    assert status["meta"]["server_configuration_ready"] is True
    assert status["meta"]["live_actions_enabled"] is False
    assert status["meta"]["existing_post_ads_enabled"] is True
    assert status["openai"]["global_credential_configured"] is True
    assert status["marketing_credentials"]["scope"] == "per_company"
    assert status["jev"]["scope"] == "per_company"
    for secret in (SYNTHETIC_META_SECRET, SYNTHETIC_WEBHOOK_VERIFIER, "synthetic-encryption-key", "synthetic-openai-key"):
        assert secret not in serialized
