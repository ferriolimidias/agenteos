from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.secret_sanitizer import sanitize_for_api, sanitize_for_log
from app.services.meta.graph_client import MetaGraphClient, MetaGraphError, MetaGraphTransport
from app.services.meta.settings import DEFAULT_MARKETING_GRAPH_API_VERSION, meta_settings


CAMPAIGN_FIELDS = "id,name,account_id,objective,status,effective_status,buying_type,created_time,updated_time"
ADSET_FIELDS = (
    "id,name,account_id,campaign_id,created_time,updated_time,configured_status,"
    "optimization_goal,billing_event,daily_budget,lifetime_budget,"
    "bid_strategy,targeting,destination_type,promoted_object,"
    "attribution_spec,status,effective_status,issues_info"
)
AD_FIELDS = "id,name,account_id,campaign_id,adset_id,created_time,updated_time,configured_status,status,effective_status,creative{id,name},issues_info"
CREATIVE_FIELDS = "id,name,account_id,object_story_spec,call_to_action_type,asset_feed_spec,url_tags,thumbnail_url,effective_object_story_id"
CUSTOM_CONVERSION_FIELDS = (
    "id,name,event_source_id,action_source_type,custom_event_type,is_archived,"
    "is_unavailable,first_fired_time,last_fired_time,creation_time,description"
)
ADS_DATASET_FIELDS = (
    "id,name,dataset_id,owner_business{id,name},owner_ad_account{id,account_id},"
    "is_crm,is_unavailable,is_created_by_business,is_eligible_for_sharing_to_ad_account,"
    "is_eligible_for_sharing_to_business,num_active_ad_set_tracked,last_fired_time"
)
OFFSITE_SIGNAL_CONTAINER_FIELDS = (
    "id,name,business{id,name},primary_container_id,is_unavailable,"
    "is_eligible_for_sharing_to_ad_account,is_eligible_for_sharing_to_business"
)
EVENT_SOURCE_GROUP_FIELDS = "id,name,event_sources"
READABLE_EFFECTIVE_STATUSES = ["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "WITH_ISSUES", "ARCHIVED"]
VIDEO_FIELDS = "id,status"
WHATSAPP_PHONE_FIELDS = "id,verified_name,display_phone_number,quality_rating"
INSIGHTS_FIELDS = (
    "account_id,account_name,account_currency,campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
    "date_start,date_stop,spend,impressions,reach,frequency,clicks,ctr,cpc,cpm,actions,"
    "cost_per_action_type,action_values,attribution_setting"
)


def normalize_ad_account_id(value: Any) -> str:
    raw = str(value or "").strip()
    return raw[4:] if raw.startswith("act_") else raw


def ad_account_path(value: Any) -> str:
    account_id = normalize_ad_account_id(value)
    if not account_id:
        raise ValueError("AD_ACCOUNT_ID_REQUIRED")
    return f"act_{account_id}"


def sanitize_graph_payload(value: Any) -> Any:
    return sanitize_for_api(value)


def sanitize_graph_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, MetaGraphError):
        error_data = sanitize_for_api(exc.error_data or {})
        return {
            "code": exc.code,
            "message": str(sanitize_for_log(str(exc)))[:240],
            "http_status": exc.status_code,
            "meta_error_code": exc.meta_code,
            "error_subcode": exc.error_subcode,
            "meta_error_type": exc.meta_error_type,
            "error_user_title": exc.error_user_title,
            "error_user_msg": exc.error_user_msg,
            "is_transient": exc.is_transient,
            "error_data": error_data or None,
            "blame_field_specs": error_data.get("blame_field_specs") if isinstance(error_data, dict) else None,
            "error_details": error_data.get("details") if isinstance(error_data, dict) else None,
            "fbtrace_id": exc.fbtrace_id,
            "graph_path": exc.graph_path,
        }
    return {"code": "unexpected_error", "message": str(sanitize_for_log(str(exc)))[:240]}


def graph_post_data(payload: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in dict(payload or {}).items():
        if isinstance(value, (dict, list)):
            data[key] = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        else:
            data[key] = value
    return data


@dataclass(frozen=True)
class MetaMarketingApiClient:
    access_token: str
    graph_version: str | None = None
    transport: MetaGraphTransport | None = None
    timeout_seconds: float = 15.0
    max_attempts: int = 2
    read_only: bool = False

    def _client(self, *, write: bool = False) -> MetaGraphClient:
        return MetaGraphClient(
            access_token=self.access_token,
            graph_api_version=self.graph_version or meta_settings().marketing_graph_api_version or DEFAULT_MARKETING_GRAPH_API_VERSION,
            transport=self.transport,
            timeout_seconds=self.timeout_seconds,
            # Graph writes are not safely retryable without an operation-level
            # idempotency key. Reads keep the configured transient retry.
            max_attempts=1 if write else self.max_attempts,
        )

    async def post(self, *_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("META_MARKETING_API_READ_ONLY")

    async def patch(self, *_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("META_MARKETING_API_WRITE_BLOCKED")

    async def delete(self, *_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("META_MARKETING_API_WRITE_BLOCKED")

    def _require_write_flags(self, *, operation: str) -> None:
        if self.read_only:
            raise RuntimeError("META_MARKETING_CREDENTIAL_READ_ONLY")
        settings = meta_settings()
        if not settings.marketing_write_enabled:
            raise RuntimeError("META_MARKETING_WRITE_DISABLED")
        if operation == "campaigns" and not settings.marketing_campaign_create_enabled:
            raise RuntimeError("META_MARKETING_CAMPAIGN_CREATE_DISABLED")
        if operation == "adsets" and not settings.marketing_adset_create_enabled:
            raise RuntimeError("META_MARKETING_ADSET_CREATE_DISABLED")
        if operation == "creatives" and not settings.marketing_creative_create_enabled:
            raise RuntimeError("META_MARKETING_CREATIVE_CREATE_DISABLED")
        if operation == "ads" and not settings.marketing_ad_create_enabled:
            raise RuntimeError("META_MARKETING_AD_CREATE_DISABLED")

    def _require_custom_conversion_write(self) -> None:
        if self.read_only:
            raise RuntimeError("META_MARKETING_CREDENTIAL_READ_ONLY")
        import os
        if not meta_settings().marketing_write_enabled:
            raise RuntimeError("META_MARKETING_WRITE_DISABLED")
        if str(os.getenv("META_MARKETING_CUSTOM_CONVERSION_CREATE_ENABLED") or "false").strip().lower() not in {"1", "true", "yes", "on"}:
            raise RuntimeError("META_MARKETING_CUSTOM_CONVERSION_CREATE_DISABLED")

    def _require_business_asset_write(self, operation: str) -> None:
        if self.read_only:
            raise RuntimeError("META_MARKETING_CREDENTIAL_READ_ONLY")
        import os
        if not meta_settings().marketing_write_enabled:
            raise RuntimeError("META_MARKETING_WRITE_DISABLED")
        flag = "META_MARKETING_ADS_DATASET_CREATE_ENABLED" if operation == "ads_dataset" else "META_MARKETING_BUSINESS_ASSET_WRITE_ENABLED"
        if str(os.getenv(flag) or "false").strip().lower() not in {"1", "true", "yes", "on"}:
            raise RuntimeError(f"{flag}_DISABLED")

    async def create_campaign(self, ad_account_id: str, payload: dict[str, Any], *, validate_only: bool = False) -> dict[str, Any]:
        self._require_write_flags(operation="campaigns")
        safe_payload = dict(payload or {})
        safe_payload["status"] = "PAUSED"
        if validate_only:
            safe_payload["validate_only"] = "true"
        return sanitize_graph_payload(await self._client(write=True)._request("POST", f"{ad_account_path(ad_account_id)}/campaigns", data=graph_post_data(safe_payload)))

    async def create_adset(self, ad_account_id: str, payload: dict[str, Any], *, validate_only: bool = False) -> dict[str, Any]:
        self._require_write_flags(operation="adsets")
        safe_payload = dict(payload or {})
        safe_payload["status"] = "PAUSED"
        if validate_only:
            safe_payload["validate_only"] = "true"
        return sanitize_graph_payload(await self._client(write=True)._request("POST", f"{ad_account_path(ad_account_id)}/adsets", data=graph_post_data(safe_payload)))

    async def update_adset(self, adset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_write_flags(operation="adsets")
        return sanitize_graph_payload(await self._client(write=True)._request("POST", str(adset_id), data=graph_post_data(payload)))

    async def upload_image(self, ad_account_id: str, content: bytes, filename: str, mime_type: str) -> dict[str, Any]:
        self._require_write_flags(operation="creatives")
        return sanitize_graph_payload(await self._client(write=True)._request("POST", f"{ad_account_path(ad_account_id)}/adimages", files={"filename": (filename, content, mime_type)}))

    async def upload_video(self, ad_account_id: str, content: bytes, filename: str, mime_type: str) -> dict[str, Any]:
        self._require_write_flags(operation="creatives")
        return sanitize_graph_payload(await self._client(write=True)._request("POST", f"{ad_account_path(ad_account_id)}/advideos", files={"source": (filename, content, mime_type)}))

    async def create_creative(self, ad_account_id: str, payload: dict[str, Any], *, validate_only: bool = False) -> dict[str, Any]:
        self._require_write_flags(operation="creatives")
        safe_payload = dict(payload or {})
        if validate_only:
            safe_payload["validate_only"] = "true"
        return sanitize_graph_payload(await self._client(write=True)._request("POST", f"{ad_account_path(ad_account_id)}/adcreatives", data=graph_post_data(safe_payload)))

    async def create_ad(self, ad_account_id: str, payload: dict[str, Any], *, validate_only: bool = False) -> dict[str, Any]:
        self._require_write_flags(operation="ads")
        safe_payload = dict(payload or {})
        safe_payload["status"] = "PAUSED"
        if validate_only:
            safe_payload["validate_only"] = "true"
        return sanitize_graph_payload(await self._client(write=True)._request("POST", f"{ad_account_path(ad_account_id)}/ads", data=graph_post_data(safe_payload)))

    async def get_ad(self, ad_id: str) -> dict[str, Any]:
        return sanitize_graph_payload(await self._client()._request("GET", ad_id, params={"fields": AD_FIELDS}))

    async def get_scoped_ad(self, ad_id: str, ad_account_id: str) -> dict[str, Any]:
        return await self._get_scoped_object(ad_id, ad_account_id, "ad")

    async def get_me(self) -> dict[str, Any]:
        return sanitize_graph_payload(await self._client()._request("GET", "me", params={"fields": "id,name"}))

    async def get_permissions(self) -> dict[str, Any]:
        return sanitize_graph_payload(await self._client()._request("GET", "me/permissions"))

    async def list_ad_accounts(
        self, *, page_counter: list[int] | None = None, max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        fields = "id,account_id,name,account_status,currency,timezone_name,business{id,name}"
        return sanitize_graph_payload(await self._client()._list_paginated(
            "me/adaccounts", fields=fields, page_counter=page_counter, max_pages=max_pages,
        ))

    async def validate_ad_account(self, ad_account_id: str) -> dict[str, Any]:
        fields = "id,account_id,name,account_status,currency,timezone_name,business{id,name}"
        return sanitize_graph_payload(await self._client()._request("GET", ad_account_path(ad_account_id), params={"fields": fields}))

    async def list_business_client_ad_accounts(self, business_id: str) -> list[dict[str, Any]]:
        """Return ad accounts explicitly shared with a Business (read-only proof)."""
        return sanitize_graph_payload(await self._client()._list_paginated(
            f"{str(business_id).strip()}/client_ad_accounts",
            fields="id,account_id,business{id}",
            max_pages=100,
        ))

    async def list_custom_conversions(self, ad_account_id: str) -> list[dict[str, Any]]:
        """Read conversion definitions owned/visible to this ad account."""
        return sanitize_graph_payload(
            await self._client()._list_paginated(
                f"{ad_account_path(ad_account_id)}/customconversions",
                fields=CUSTOM_CONVERSION_FIELDS,
            )
        )

    async def list_business_ads_datasets(self, business_id: str) -> list[dict[str, Any]]:
        return sanitize_graph_payload(
            await self._client()._list_paginated(f"{str(business_id).strip()}/ads_dataset", fields=ADS_DATASET_FIELDS)
        )

    async def list_business_offsite_signal_containers(self, business_id: str) -> list[dict[str, Any]]:
        return sanitize_graph_payload(
            await self._client()._list_paginated(
                f"{str(business_id).strip()}/client_offsite_signal_container_business_objects",
                fields=OFFSITE_SIGNAL_CONTAINER_FIELDS,
            )
        )

    async def list_business_event_source_groups(self, business_id: str) -> list[dict[str, Any]]:
        return sanitize_graph_payload(
            await self._client()._list_paginated(f"{str(business_id).strip()}/event_source_groups", fields=EVENT_SOURCE_GROUP_FIELDS)
        )

    async def create_business_ads_dataset(self, business_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an AdsDataset only behind an explicit deployment flag."""
        self._require_business_asset_write("ads_dataset")
        return sanitize_graph_payload(
            await self._client(write=True)._request(
                "POST", f"{str(business_id).strip()}/ads_dataset", data=graph_post_data(dict(payload or {}))
            )
        )

    async def claim_business_custom_conversion(self, business_id: str, custom_conversion_id: str) -> dict[str, Any]:
        """Claiming is a business-asset mutation and is disabled by default."""
        self._require_business_asset_write("business_asset")
        return sanitize_graph_payload(
            await self._client(write=True)._request(
                "POST",
                f"{str(business_id).strip()}/claim_custom_conversions",
                data={"custom_conversion_id": str(custom_conversion_id).strip()},
            )
        )

    async def create_custom_conversion(self, ad_account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create only an explicitly enabled, tenant-scoped conversion definition.

        Standard CAPI events remain producer-owned. This endpoint is for the
        optional Meta conversion definition selected by Ads Manager, not for
        registering a standard event or replaying an event.
        """
        self._require_custom_conversion_write()
        return sanitize_graph_payload(
            await self._client(write=True)._request(
                "POST",
                f"{ad_account_path(ad_account_id)}/customconversions",
                data=graph_post_data(dict(payload or {})),
            )
        )

    async def list_pages(self) -> list[dict[str, Any]]:
        fields = "id,name,tasks,instagram_business_account{id,username},connected_instagram_account{id,username}"
        return sanitize_graph_payload(await self._client()._list_paginated("me/accounts", fields=fields))

    async def list_page_published_posts(
        self, page_id: str, *, limit: int = 25, after: str | None = None,
    ) -> dict[str, Any]:
        """Read one bounded page of published Page posts; never follows a Graph URL."""
        params: dict[str, Any] = {
            "fields": "id,message,created_time,permalink_url,full_picture,from{id},attachments{media_type,url,media{image{src},source},subattachments{media_type,url,media{image{src},source}}}",
            "limit": max(1, min(int(limit or 25), 50)),
        }
        if after:
            params["after"] = str(after)[:500]
        body = await self._client()._request("GET", f"{str(page_id).strip()}/published_posts", params=params)
        return sanitize_graph_payload(body)

    async def list_instagram_media_page(
        self, instagram_account_id: str, *, limit: int = 25, after: str | None = None,
    ) -> dict[str, Any]:
        """Read one bounded page of media for a Page-linked Instagram account."""
        params: dict[str, Any] = {
            "fields": "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp",
            "limit": max(1, min(int(limit or 25), 50)),
        }
        if after:
            params["after"] = str(after)[:500]
        body = await self._client()._request("GET", f"{str(instagram_account_id).strip()}/media", params=params)
        return sanitize_graph_payload(body)

    async def get_page_published_post(self, post_id: str) -> dict[str, Any]:
        fields = "id,message,created_time,permalink_url,full_picture,from{id},attachments{media_type,url,media{image{src},source}}"
        return sanitize_graph_payload(await self._client()._request("GET", str(post_id).strip(), params={"fields": fields}))

    async def get_instagram_media(self, media_id: str) -> dict[str, Any]:
        fields = "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp"
        return sanitize_graph_payload(await self._client()._request("GET", str(media_id).strip(), params={"fields": fields}))

    async def list_campaigns(
        self, ad_account_id: str, *, page_counter: list[int] | None = None, max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        return sanitize_graph_payload(await self._client()._list_paginated(
            f"{ad_account_path(ad_account_id)}/campaigns", fields=CAMPAIGN_FIELDS,
            page_counter=page_counter, max_pages=max_pages,
        ))

    async def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        return sanitize_graph_payload(await self._client()._request("GET", campaign_id, params={"fields": CAMPAIGN_FIELDS}))

    async def get_scoped_campaign(self, campaign_id: str, ad_account_id: str) -> dict[str, Any]:
        return await self._get_scoped_object(campaign_id, ad_account_id, "campaign")

    async def get_adset(self, adset_id: str) -> dict[str, Any]:
        return sanitize_graph_payload(await self._client()._request("GET", adset_id, params={"fields": ADSET_FIELDS}))

    async def get_scoped_adset(self, adset_id: str, ad_account_id: str) -> dict[str, Any]:
        return await self._get_scoped_object(adset_id, ad_account_id, "adset")

    async def list_adsets(
        self, ad_account_id: str, *, campaign_id: str | None = None,
        page_counter: list[int] | None = None, max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        params = {"fields": ADSET_FIELDS, "limit": 100, "effective_status": json.dumps(READABLE_EFFECTIVE_STATUSES)}
        if campaign_id:
            params["filtering"] = json.dumps([{"field": "campaign.id", "operator": "EQUAL", "value": str(campaign_id)}])
        fields = str(params.pop("fields"))
        params.pop("limit", None)
        return sanitize_graph_payload(await self._client()._list_paginated(
            f"{ad_account_path(ad_account_id)}/adsets", fields=fields, extra_params=params,
            page_counter=page_counter, max_pages=max_pages,
        ))

    async def list_ads(
        self, ad_account_id: str, *, campaign_id: str | None = None, adset_id: str | None = None,
        page_counter: list[int] | None = None, max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        params = {"fields": AD_FIELDS, "limit": 100, "effective_status": json.dumps(READABLE_EFFECTIVE_STATUSES)}
        filters = []
        if campaign_id:
            filters.append({"field": "campaign.id", "operator": "EQUAL", "value": str(campaign_id)})
        if adset_id:
            filters.append({"field": "adset.id", "operator": "EQUAL", "value": str(adset_id)})
        if filters:
            params["filtering"] = json.dumps(filters)
        fields = str(params.pop("fields"))
        params.pop("limit", None)
        return sanitize_graph_payload(await self._client()._list_paginated(
            f"{ad_account_path(ad_account_id)}/ads", fields=fields, extra_params=params,
            page_counter=page_counter, max_pages=max_pages,
        ))

    async def list_ads_for_adset(self, adset_id: str, *, page_counter: list[int] | None = None) -> list[dict[str, Any]]:
        """Read an ad set's children without account-level status filtering.

        Meta can lag on account-level filtered reads immediately after an ad
        write. The ad set child edge is the authoritative narrow read used by
        idempotent post-write reconciliation.
        """
        return sanitize_graph_payload(await self._client()._list_paginated(
            f"{str(adset_id)}/ads", fields=AD_FIELDS, page_counter=page_counter,
        ))

    async def list_creatives(self, ad_account_id: str, *, page_counter: list[int] | None = None) -> list[dict[str, Any]]:
        """Read account creatives so a partial execution can reconcile by deterministic name."""
        return sanitize_graph_payload(
            await self._client()._list_paginated(
                f"{ad_account_path(ad_account_id)}/adcreatives",
                fields=CREATIVE_FIELDS,
                page_counter=page_counter,
            )
        )

    async def get_creative(self, creative_id: str) -> dict[str, Any]:
        return sanitize_graph_payload(await self._client()._request("GET", creative_id, params={"fields": CREATIVE_FIELDS}))

    async def get_scoped_creative(self, creative_id: str, ad_account_id: str) -> dict[str, Any]:
        return await self._get_scoped_object(creative_id, ad_account_id, "creative")

    async def _get_scoped_object(self, object_id: str, ad_account_id: str, object_type: str) -> dict[str, Any]:
        """Read only the account binding first; disclose details after exact match."""
        getters = {
            "campaign": self.get_campaign,
            "adset": self.get_adset,
            "ad": self.get_ad,
            "creative": self.get_creative,
        }
        getter = getters.get(object_type)
        if getter is None:
            raise ValueError("META_MARKETING_ASSET_TYPE_INVALID")
        object_key = str(object_id or "").strip()
        if not object_key:
            raise ValueError("META_MARKETING_ASSET_ID_REQUIRED")
        binding = await self._client()._request("GET", object_key, params={"fields": "id,account_id"})
        expected = normalize_ad_account_id(ad_account_id)
        actual = normalize_ad_account_id((binding or {}).get("account_id"))
        if str((binding or {}).get("id") or "") != object_key or not actual or actual != expected:
            raise RuntimeError("MARKETING_ASSET_OUT_OF_SCOPE")
        details = await getter(object_key)
        if normalize_ad_account_id(details.get("account_id")) != expected:
            raise RuntimeError("MARKETING_ASSET_OUT_OF_SCOPE")
        return details

    async def get_video(self, video_id: str) -> dict[str, Any]:
        return sanitize_graph_payload(await self._client()._request("GET", video_id, params={"fields": VIDEO_FIELDS}))

    async def get_whatsapp_phone_number(self, phone_number_id: str) -> dict[str, Any]:
        return sanitize_graph_payload(await self._client()._request("GET", str(phone_number_id), params={"fields": WHATSAPP_PHONE_FIELDS}))

    async def list_adcreatives(self, ad_account_id: str, *, page_counter: list[int] | None = None) -> list[dict[str, Any]]:
        return sanitize_graph_payload(await self._client()._list_paginated(
            f"{ad_account_path(ad_account_id)}/adcreatives", fields=CREATIVE_FIELDS, page_counter=page_counter,
        ))

    async def get_insights(
        self,
        object_id: str,
        *,
        level: str = "account",
        date_preset: str | None = "last_30d",
        since: str | None = None,
        until: str | None = None,
        time_increment: int | None = None,
        attribution_setting: str | None = None,
        account_scope: bool = False,
        page_counter: list[int] | None = None,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """Read paginated Insights; no campaign, adset or ad mutation path is used."""
        if level not in {"account", "campaign", "adset", "ad"}:
            raise ValueError("META_INSIGHTS_LEVEL_INVALID")
        path = f"{ad_account_path(object_id)}/insights" if level == "account" or account_scope else f"{str(object_id).strip()}/insights"
        params: dict[str, Any] = {}
        if level != "account" and account_scope:
            params["level"] = level
        if since and until:
            params["time_range"] = json.dumps({"since": since, "until": until}, separators=(",", ":"))
        elif date_preset:
            params["date_preset"] = date_preset
        if time_increment is not None:
            params["time_increment"] = max(1, min(int(time_increment), 90))
        if attribution_setting:
            params["action_attribution_windows"] = json.dumps([attribution_setting])
        return sanitize_graph_payload(await self._client()._list_paginated(
            path, fields=INSIGHTS_FIELDS, limit=100, extra_params=params,
            page_counter=page_counter, max_pages=max_pages,
        ))
