from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.secret_sanitizer import sanitize_for_log
from app.services.meta.settings import DEFAULT_GRAPH_API_VERSION


# ``account_type`` is not a valid field in the Page nested Instagram
# expansion on the currently configured Graph API version. Requesting it
# makes the whole Page collection fail with Graph error 100, so account type
# remains optional and is populated only when the API returns it.
INSTAGRAM_DISCOVERY_PAGE_FIELDS = (
    "id,name,picture{url},tasks,access_token,"
    "instagram_business_account{id,username,name,profile_picture_url},"
    "connected_instagram_account{id,username,name,profile_picture_url}"
)

INSTAGRAM_VALIDATION_PAGE_FIELDS = (
    "id,name,"
    "instagram_business_account{id,username,name,profile_picture_url},"
    "connected_instagram_account{id,username,name,profile_picture_url}"
)


class MetaGraphTransport(Protocol):
    async def get(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        ...

    async def post(self, url: str, *, data: dict[str, Any] | None = None, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None, files: dict[str, Any] | None = None) -> Any:
        ...


class MetaGraphError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        meta_code: str | None = None,
        error_subcode: str | None = None,
        meta_error_type: str | None = None,
        error_user_title: str | None = None,
        error_user_msg: str | None = None,
        is_transient: bool | None = None,
        error_data: dict[str, Any] | None = None,
        fbtrace_id: str | None = None,
        graph_path: str | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.meta_code = meta_code
        self.error_subcode = error_subcode
        self.meta_error_type = meta_error_type
        self.error_user_title = error_user_title
        self.error_user_msg = error_user_msg
        self.is_transient = is_transient
        self.error_data = error_data if isinstance(error_data, dict) else None
        self.fbtrace_id = fbtrace_id
        self.graph_path = graph_path
        super().__init__(message)


@dataclass(frozen=True)
class MetaGraphClient:
    access_token: str | None = None
    graph_api_version: str = DEFAULT_GRAPH_API_VERSION
    transport: MetaGraphTransport | None = None
    timeout_seconds: float = 15.0
    max_attempts: int = 2

    def _url(self, path: str) -> str:
        clean_path = str(path or "").strip().lstrip("/")
        return f"https://graph.facebook.com/{self.graph_api_version}/{clean_path}"

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            return {}
        return {"Authorization": f"Bearer {self.access_token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        close_client = False
        transport = self.transport
        if transport is None:
            transport = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds, connect=5.0))
            close_client = True
        try:
            last_error: MetaGraphError | None = None
            for attempt in range(self.max_attempts):
                try:
                    if method == "GET":
                        response = await transport.get(self._url(path), headers=self._headers(), params=kwargs.get("params"))
                    else:
                        response = await transport.post(
                            self._url(path),
                            headers=self._headers(),
                            data=kwargs.get("data"),
                            json=kwargs.get("json"),
                            files=kwargs.get("files"),
                        )
                except (httpx.TimeoutException, TimeoutError) as exc:
                    last_error = MetaGraphError("timeout", "Tempo esgotado ao consultar a Meta.", status_code=None)
                    if attempt + 1 >= self.max_attempts:
                        raise last_error from exc
                    await asyncio.sleep(min(4.0, 0.5 * (2 ** attempt)))
                    continue
                except Exception as exc:
                    last_error = MetaGraphError("temporary_error", "Falha temporaria ao consultar a Meta.", status_code=None)
                    if attempt + 1 >= self.max_attempts:
                        raise last_error from exc
                    await asyncio.sleep(min(4.0, 0.5 * (2 ** attempt)))
                    continue

                status = int(getattr(response, "status_code", 0) or 0)
                body = self._json_body(response)
                if 200 <= status < 300:
                    return body
                error = self._classify_error(status, body, path=path)
                if error.code in {"rate_limit", "temporary_error"} and attempt + 1 < self.max_attempts:
                    await asyncio.sleep(self._retry_delay(response, attempt))
                    continue
                raise error
            raise last_error or MetaGraphError("unexpected_response", "Resposta inesperada da Meta.")
        finally:
            if close_client:
                await transport.aclose()

    @staticmethod
    def _retry_delay(response: Any, attempt: int) -> float:
        headers = getattr(response, "headers", {}) or {}
        try:
            retry_after = float(headers.get("Retry-After") or 0)
        except (TypeError, ValueError):
            retry_after = 0
        if retry_after > 0:
            return min(30.0, max(1.0, retry_after))
        return min(8.0, 1.0 * (2 ** attempt))

    @staticmethod
    def _json_body(response: Any) -> dict[str, Any]:
        try:
            body = response.json()
        except Exception:
            return {}
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _classify_error(status: int, body: dict[str, Any], *, path: str | None = None) -> MetaGraphError:
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        meta_code = str(error.get("code") or "")
        subcode = str(error.get("error_subcode") or "")
        error_type = str(error.get("type") or "")
        error_user_title = str(error.get("error_user_title") or "")
        error_user_msg = str(error.get("error_user_msg") or "")
        is_transient = error.get("is_transient") if isinstance(error.get("is_transient"), bool) else None
        error_data = error.get("error_data") if isinstance(error.get("error_data"), dict) else None
        fbtrace_id = str(error.get("fbtrace_id") or "")
        raw_message = str(error.get("message") or f"status {status}")
        safe_message = str(sanitize_for_log(raw_message))[:240]
        lowered = safe_message.lower()

        code = "permanent_error"
        if "unsupported get request" in lowered:
            code = "invalid_object_id"
        elif "unsupported field" in lowered or "nonexisting field" in lowered or meta_code == "100":
            code = "unsupported_field"
        elif meta_code in {"190", "102"} or status == 401:
            code = "token_expired" if "expired" in lowered or subcode else "invalid_auth"
        elif status == 403 and ("permission" in lowered or "permissions" in lowered):
            code = "missing_permission"
        elif status == 403:
            code = "token_expired" if "expired" in lowered or subcode else "invalid_auth"
        elif status == 429 or meta_code in {"4", "17", "613"}:
            code = "rate_limit"
        elif status >= 500:
            code = "temporary_error"
        elif "permission" in lowered or "permissions" in lowered:
            code = "missing_permission"
        elif "review" in lowered:
            code = "app_review_pending"
        elif "not found" in lowered and "waba" in lowered:
            code = "waba_not_found"
        elif "not found" in lowered and "phone" in lowered:
            code = "phone_number_not_found"
        elif "already" in lowered and "phone" in lowered:
            code = "phone_number_already_linked"
        elif not error:
            code = "unexpected_response"
        return MetaGraphError(
            code,
            safe_message,
            status_code=status,
            meta_code=meta_code or None,
            error_subcode=subcode or None,
            meta_error_type=error_type or None,
            error_user_title=str(sanitize_for_log(error_user_title))[:240] or None,
            error_user_msg=str(sanitize_for_log(error_user_msg))[:500] or None,
            is_transient=is_transient,
            error_data=error_data,
            fbtrace_id=fbtrace_id or None,
            graph_path=str(path or "").strip() or None,
        )

    async def exchange_code(self, *, code: str, app_id: str, app_secret: str, redirect_uri: str | None = None) -> dict[str, Any]:
        data = {
            "client_id": app_id,
            "client_secret": app_secret,
            "code": code,
        }
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
        return await self._request("POST", "oauth/access_token", data=data)

    async def debug_token(self, *, input_token: str, app_access_token: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            "debug_token",
            params={"input_token": input_token, "access_token": app_access_token},
        )

    async def _list_paginated(
        self,
        path: str,
        *,
        fields: str,
        limit: int = 100,
        extra_params: dict[str, Any] | None = None,
        page_counter: list[int] | None = None,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """Read every Graph page without following an untrusted host or path."""
        items: list[dict[str, Any]] = []
        after: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(max(1, min(int(max_pages or 100), 100))):
            params: dict[str, Any] = {**dict(extra_params or {}), "fields": fields, "limit": max(1, min(int(limit or 100), 100))}
            if after:
                params["after"] = after
            body = await self._request("GET", path, params=params)
            if page_counter is not None:
                page_counter[0] += 1
            data = body.get("data") if isinstance(body.get("data"), list) else []
            items.extend(item for item in data if isinstance(item, dict))
            paging = body.get("paging") if isinstance(body.get("paging"), dict) else {}
            cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
            next_cursor = str(cursors.get("after") or "").strip()
            if not next_cursor:
                next_url = str(paging.get("next") or "").strip()
                next_cursor = str(parse_qs(urlparse(next_url).query).get("after", [""])[0]).strip() if next_url else ""
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            after = next_cursor
        return items

    async def list_user_pages(self) -> list[dict[str, Any]]:
        return await self._list_paginated("me/accounts", fields=INSTAGRAM_DISCOVERY_PAGE_FIELDS)

    async def list_assigned_pages(self, business_user_id: str) -> list[dict[str, Any]]:
        return await self._list_paginated(f"{business_user_id}/assigned_pages", fields=INSTAGRAM_DISCOVERY_PAGE_FIELDS)

    async def list_assigned_businesses(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "me/businesses", params={"fields": "id,name"})
        data = body.get("data") if isinstance(body.get("data"), list) else []
        return [item for item in data if isinstance(item, dict)]

    async def list_owned_wabas(self, business_id: str) -> list[dict[str, Any]]:
        body = await self._request(
            "GET",
            f"{business_id}/owned_whatsapp_business_accounts",
            params={"fields": "id,name,account_review_status,business_verification_status,timezone_id,message_template_namespace"},
        )
        data = body.get("data") if isinstance(body.get("data"), list) else []
        return [item for item in data if isinstance(item, dict)]

    async def list_waba_phone_numbers(self, waba_id: str) -> list[dict[str, Any]]:
        fields = ",".join(
            [
                "id",
                "display_phone_number",
                "verified_name",
                "quality_rating",
                "code_verification_status",
                "platform_type",
                "throughput",
                "webhook_configuration",
                "status",
            ]
        )
        body = await self._request("GET", f"{waba_id}/phone_numbers", params={"fields": fields})
        data = body.get("data") if isinstance(body.get("data"), list) else []
        return [item for item in data if isinstance(item, dict)]

    async def get_phone_number(self, phone_number_id: str) -> dict[str, Any]:
        fields = "id,display_phone_number,verified_name,quality_rating,code_verification_status,platform_type,status"
        return await self._request("GET", phone_number_id, params={"fields": fields})

    async def list_waba_subscribed_apps(self, waba_id: str) -> list[dict[str, Any]]:
        body = await self._request("GET", f"{waba_id}/subscribed_apps")
        data = body.get("data") if isinstance(body.get("data"), list) else []
        return [item for item in data if isinstance(item, dict)]

    async def subscribe_waba(self, waba_id: str) -> dict[str, Any]:
        return await self._request("POST", f"{waba_id}/subscribed_apps")

    @staticmethod
    def _subscribed_app_ids(apps: list[dict[str, Any]]) -> set[str]:
        ids: set[str] = set()
        for app in apps:
            if not isinstance(app, dict):
                continue
            for value in (app.get("id"), app.get("app_id")):
                if value:
                    ids.add(str(value).strip())
            whatsapp_data = app.get("whatsapp_business_api_data")
            if isinstance(whatsapp_data, dict) and whatsapp_data.get("id"):
                ids.add(str(whatsapp_data["id"]).strip())
        return {value for value in ids if value}

    async def ensure_waba_subscription(self, waba_id: str, app_id: str) -> dict[str, Any]:
        """Subscribe this app only when absent, then prove it with a fresh GET."""
        waba = str(waba_id or "").strip()
        app = str(app_id or "").strip()
        if not waba or not app:
            raise MetaGraphError("subscription_not_configured", "WABA subscription identifiers are incomplete.")

        before = await self.list_waba_subscribed_apps(waba)
        if app in self._subscribed_app_ids(before):
            return {"subscribed": True, "changed": False, "apps": before}

        await self.subscribe_waba(waba)
        after = await self.list_waba_subscribed_apps(waba)
        if app not in self._subscribed_app_ids(after):
            raise MetaGraphError("subscription_not_confirmed", "WABA app subscription could not be confirmed.")
        return {"subscribed": True, "changed": True, "apps": after}

    async def unsubscribe_waba(self, waba_id: str) -> dict[str, Any]:
        return await self._request("POST", f"{waba_id}/subscribed_apps", data={"subscribed_fields": ""})

    async def get_instagram_page(self, page_id: str) -> dict[str, Any]:
        return await self._request("GET", page_id, params={"fields": INSTAGRAM_VALIDATION_PAGE_FIELDS})

    async def get_instagram_account(self, account_id: str) -> dict[str, Any]:
        return await self._request("GET", account_id, params={"fields": "id,username,profile_picture_url"})

    async def get_instagram_comment(self, comment_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            comment_id,
            params={"fields": "id,text,from{id,username},media{id,owner{id}}"},
        )

    async def list_instagram_media(
        self,
        account_id: str,
        *,
        after: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        fields = ",".join(
            [
                "id",
                "caption",
                "media_type",
                "media_product_type",
                "media_url",
                "thumbnail_url",
                "permalink",
                "timestamp",
            ]
        )
        params: dict[str, Any] = {"fields": fields, "limit": max(1, min(int(limit or 25), 100))}
        if after:
            params["after"] = str(after).strip()
        return await self._request("GET", f"{account_id}/media", params=params)

    async def reply_to_instagram_comment(self, comment_id: str, message: str) -> dict[str, Any]:
        return await self._request("POST", f"{comment_id}/replies", data={"message": message})

    async def send_instagram_private_reply(self, account_id: str, comment_id: str, message: str) -> dict[str, Any]:
        return await self._request("POST", f"{account_id}/messages", json={"recipient": {"comment_id": comment_id}, "message": {"text": message}})
