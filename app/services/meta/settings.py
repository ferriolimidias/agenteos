from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_GRAPH_API_VERSION = "v25.0"
DEFAULT_MARKETING_GRAPH_API_VERSION = "v26.0"


@dataclass(frozen=True)
class MetaAppSettings:
    app_id: str | None
    app_secret: str | None
    embedded_signup_config_id: str | None
    graph_api_version: str
    webhook_verify_token: str | None
    redirect_uri: str | None
    live_actions_enabled: bool
    credential_encryption_key: str | None
    marketing_graph_api_version: str
    marketing_write_enabled: bool
    marketing_campaign_create_enabled: bool
    marketing_adset_create_enabled: bool
    marketing_creative_create_enabled: bool
    marketing_ad_create_enabled: bool

    @property
    def public_configured(self) -> bool:
        return bool(self.app_id and self.embedded_signup_config_id and self.graph_api_version)

    @property
    def server_configured(self) -> bool:
        return bool(self.public_configured and self.app_secret and self.credential_encryption_key)


def _clean_env(name: str) -> str | None:
    value = str(os.getenv(name) or "").strip()
    return value or None


def _env_enabled(name: str) -> bool:
    return str(os.getenv(name) or "false").strip().lower() in {"1", "true", "yes", "sim", "on"}


def meta_settings() -> MetaAppSettings:
    version = _clean_env("META_GRAPH_API_VERSION") or DEFAULT_GRAPH_API_VERSION
    if not version.startswith("v"):
        version = f"v{version}"
    marketing_version = _clean_env("META_MARKETING_GRAPH_VERSION") or DEFAULT_MARKETING_GRAPH_API_VERSION
    if not marketing_version.startswith("v"):
        marketing_version = f"v{marketing_version}"
    return MetaAppSettings(
        app_id=_clean_env("META_APP_ID"),
        app_secret=_clean_env("META_APP_SECRET"),
        embedded_signup_config_id=_clean_env("META_EMBEDDED_SIGNUP_CONFIG_ID"),
        graph_api_version=version,
        webhook_verify_token=_clean_env("META_WEBHOOK_VERIFY_TOKEN"),
        redirect_uri=_clean_env("META_REDIRECT_URI"),
        live_actions_enabled=_env_enabled("META_LIVE_ACTIONS_ENABLED"),
        credential_encryption_key=_clean_env("META_CREDENTIAL_ENCRYPTION_KEY"),
        marketing_graph_api_version=marketing_version,
        marketing_write_enabled=_env_enabled("META_MARKETING_WRITE_ENABLED"),
        marketing_campaign_create_enabled=_env_enabled("META_MARKETING_CAMPAIGN_CREATE_ENABLED"),
        marketing_adset_create_enabled=_env_enabled("META_MARKETING_ADSET_CREATE_ENABLED"),
        marketing_creative_create_enabled=_env_enabled("META_MARKETING_CREATIVE_CREATE_ENABLED"),
        marketing_ad_create_enabled=_env_enabled("META_MARKETING_AD_CREATE_ENABLED"),
    )
