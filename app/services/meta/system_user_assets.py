"""Read-only discovery and revalidation for the Ferrioli Meta System User."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.services.meta.graph_client import MetaGraphClient, MetaGraphError
from app.services.meta.settings import meta_settings


ASSET_FIELDS = {
    "business": "id,name",
    "ad_account": "id,account_id,name,account_status,currency,timezone_name,business{id,name}",
    "page": "id,name,tasks,instagram_business_account{id,username,name,profile_picture_url}",
    # Business edges return InstagramBusinessAsset, whose canonical account
    # identifier is ig_user_id. Page nested instagram_business_account objects
    # still use the IGUser shape and are normalized by _record's `id` fallback.
    "instagram_account": "id,ig_user_id,ig_username",
    "dataset": "id,name,dataset_id,owner_business{id,name},is_crm,is_unavailable",
    "pixel": "id,name,owner_business{id,name}",
}


def _business(row: dict[str, Any] | None) -> tuple[str | None, str | None]:
    value = row if isinstance(row, dict) else {}
    return (str(value.get("id") or "").strip() or None, str(value.get("name") or "").strip() or None)


def _record(
    asset_type: str,
    row: dict[str, Any],
    *,
    business_id: str | None = None,
    business_name: str | None = None,
    access_path: str,
) -> dict[str, Any] | None:
    if asset_type == "ad_account":
        external_id = str(row.get("account_id") or row.get("id") or "").strip()
        if external_id.startswith("act_"):
            external_id = external_id[4:]
    elif asset_type == "dataset":
        external_id = str(row.get("dataset_id") or row.get("id") or "").strip()
    elif asset_type == "instagram_account":
        external_id = str(row.get("ig_user_id") or row.get("id") or "").strip()
    else:
        external_id = str(row.get("id") or "").strip()
    if not external_id:
        return None
    owner_id, owner_name = _business(row.get("business") or row.get("owner_business"))
    owner_business_id, owner_business_name = owner_id, owner_name
    if access_path.startswith("business_") and business_id:
        # ``business_id`` is the business through which this System User sees
        # the asset; the owning business remains separately recorded so tenant
        # linking can prove ownership or sharing.
        owner_id, owner_name = business_id, business_name
    if asset_type == "business":
        owner_id, owner_name = external_id, str(row.get("name") or "").strip() or None
    meta: dict[str, Any] = {}
    for key in ("account_status", "currency", "timezone_name", "is_crm", "is_unavailable", "username", "tasks"):
        value = row.get(key)
        if isinstance(value, (str, int, bool, list)):
            meta[key] = value
    if asset_type == "instagram_account":
        username = row.get("ig_username") or row.get("username")
        if isinstance(username, str) and username.strip():
            meta["username"] = username.strip()[:120]
    if asset_type == "ad_account" and row.get("id"):
        meta["graph_id"] = str(row["id"])
    if owner_business_id:
        meta["owner_business_id"] = owner_business_id
    if owner_business_name:
        meta["owner_business_name"] = owner_business_name
    instagram = row.get("instagram_business_account")
    if asset_type == "page" and isinstance(instagram, dict) and instagram.get("id"):
        meta["instagram_account_id"] = str(instagram["id"])
        meta["instagram_username"] = str(instagram.get("username") or "")[:120]
    access_business_id = (business_id or owner_id) if asset_type != "business" else None
    access_business_name = (business_name or owner_name) if asset_type != "business" else None
    return {
        "asset_type": asset_type,
        "external_id": external_id,
        "name": str(row.get("name") or row.get("username") or "").strip()[:240] or None,
        "business_id": owner_id or business_id,
        "business_name": owner_name or business_name,
        "access_path": access_path,
        "business_access": ([{
            "business_id": access_business_id,
            "business_name": access_business_name,
            "access_path": access_path,
        }] if access_business_id else []),
        "permissions": row.get("tasks") if isinstance(row.get("tasks"), list) else [],
        "metadata": meta,
    }


def system_user_graph(token: str, *, transport=None) -> MetaGraphClient:
    return MetaGraphClient(
        access_token=token,
        graph_api_version=meta_settings().marketing_graph_api_version,
        transport=transport,
        timeout_seconds=12.0,
        max_attempts=1,
    )


async def _list_pages(graph: MetaGraphClient, path: str, fields: str, max_pages: int) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    after = ""
    seen: set[str] = set()
    page_limit = max(1, min(int(max_pages or 1), 50))
    for page_index in range(page_limit):
        params: dict[str, Any] = {"fields": fields, "limit": 100}
        if after:
            params["after"] = after
        body = await graph._request("GET", path, params=params)
        data = body.get("data") if isinstance(body.get("data"), list) else []
        rows.extend(item for item in data if isinstance(item, dict))
        paging = body.get("paging") if isinstance(body.get("paging"), dict) else {}
        cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
        next_cursor = str(cursors.get("after") or "").strip()
        next_url = str(paging.get("next") or "").strip()
        if not next_cursor:
            next_cursor = str(parse_qs(urlparse(next_url).query).get("after", [""])[0]).strip() if next_url else ""
        if next_url and not next_cursor:
            return rows, True
        if next_cursor in seen:
            return rows, True
        if not next_cursor:
            return rows, False
        seen.add(next_cursor)
        after = next_cursor
        if page_index + 1 >= page_limit:
            return rows, True
    return rows, False


async def validate_system_user_token(token: str, *, transport=None) -> dict[str, Any]:
    graph = system_user_graph(token, transport=transport)
    profile = await graph._request("GET", "me", params={"fields": "id,name"})
    if not str(profile.get("id") or "").strip():
        raise MetaGraphError("unexpected_response", "A Meta não retornou a identidade do usuário do sistema.")
    permission_error_code = None
    try:
        permission_result = await graph._request("GET", "me/permissions")
    except MetaGraphError as exc:
        permission_result = {}
        permission_error_code = exc.code
    rows = permission_result.get("data") if isinstance(permission_result.get("data"), list) else []
    permissions = sorted({
        str(row.get("permission") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("status") or "").lower() == "granted"
        and str(row.get("permission") or "").strip()
    })
    app_id = None
    app_name = None
    expires_at = None
    expiry_status = "unknown"
    settings = meta_settings()
    # Token debugging is optional: `/me` and `/me/permissions` remain the
    # actual validation. Do not fail a valid credential if app-level debug
    # access is unavailable.
    if settings.app_id and settings.app_secret:
        try:
            debug = await graph.debug_token(
                input_token=token,
                app_access_token=f"{settings.app_id}|{settings.app_secret}",
            )
            data = debug.get("data") if isinstance(debug.get("data"), dict) else {}
            if data.get("is_valid") is False:
                raise MetaGraphError("token_app_mismatch", "A Meta não confirmou a validade deste token para o aplicativo configurado.")
            if data.get("is_valid") is True:
                app_id = str(data.get("app_id") or "").strip() or None
                if app_id and app_id != str(settings.app_id).strip():
                    raise MetaGraphError(
                        "token_app_mismatch",
                        "O token foi emitido para outro aplicativo Meta.",
                    )
                app_name = str(data.get("application") or "").strip()[:200] or None
                try:
                    expires_epoch = int(data.get("expires_at") or 0)
                    expires_at = datetime.fromtimestamp(expires_epoch, tz=timezone.utc) if expires_epoch > 0 else None
                    expiry_status = "known_expiring" if expires_at else "no_expiration_reported"
                except (TypeError, ValueError, OverflowError):
                    expires_at = None
                    expiry_status = "unknown"
        except MetaGraphError as exc:
            # A confirmed app mismatch is a credential validation failure;
            # availability/permission errors from the optional debug endpoint
            # remain non-fatal and leave app/expiry fields unknown.
            if exc.code == "token_app_mismatch":
                raise
        except Exception:
            # Never fail a valid `/me` check solely because optional token
            # debugging is unavailable; no error text is propagated/logged.
            pass
    return {
        "system_user_id": str(profile.get("id") or "").strip() or None,
        "system_user_name": str(profile.get("name") or "").strip()[:200] or None,
        # /me is an identity check, not proof of token type or membership in
        # Ferrioli's designated Business Portfolio.
        "system_user_identity_state": "unknown",
        "app_binding_state": "verified" if app_id else "unknown",
        "app_id": app_id,
        "app_name": app_name,
        "granted_permissions": permissions,
        "permissions_status": "verified" if permission_error_code is None else "unknown",
        "permissions_error_code": permission_error_code,
        "validated_at": datetime.now(timezone.utc),
        "expires_at": expires_at,
        "expiry_status": expiry_status,
    }


async def discover_system_user_assets(token: str, *, transport=None, max_pages: int = 3) -> dict[str, Any]:
    """Discover only objects returned by Graph; individual unsupported edges are partial."""
    graph = system_user_graph(token, transport=transport)
    assets: dict[tuple[str, str], dict[str, Any]] = {}
    errors: list[dict[str, str]] = []

    def add(asset: dict[str, Any] | None) -> None:
        if asset:
            key = (asset["asset_type"], asset["external_id"])
            previous = assets.get(key)
            if previous is None:
                assets[key] = asset
                return
            contexts = list(previous.get("business_access") or [])
            for context in asset.get("business_access") or []:
                if context not in contexts:
                    contexts.append(context)
            previous["business_access"] = contexts
            if not previous.get("metadata", {}).get("owner_business_id") and asset.get("metadata", {}).get("owner_business_id"):
                previous["metadata"].update({
                    "owner_business_id": asset["metadata"]["owner_business_id"],
                    "owner_business_name": asset["metadata"].get("owner_business_name"),
                })

    async def edge(path: str, asset_type: str, *, business_id: str | None = None,
                   business_name: str | None = None, access_path: str) -> list[dict[str, Any]]:
        try:
            rows, truncated = await _list_pages(graph, path, ASSET_FIELDS[asset_type], max_pages)
            for row in rows:
                add(_record(asset_type, row, business_id=business_id,
                            business_name=business_name, access_path=access_path))
            if truncated:
                errors.append({"asset_type": asset_type, "business_id": business_id or "", "code": "asset_pagination_limit_reached"})
            return rows
        except MetaGraphError as exc:
            errors.append({"asset_type": asset_type, "business_id": business_id or "", "code": exc.code})
            return []

    try:
        businesses, business_list_truncated = await _list_pages(graph, "me/businesses", ASSET_FIELDS["business"], max_pages)
    except MetaGraphError as exc:
        # Some System User tokens cannot enumerate their Business portfolio
        # through this edge. Continue read-only discovery for directly assigned
        # assets and report this capability as partial, never as "no business".
        businesses = []
        business_list_truncated = False
        errors.append({"asset_type": "business", "business_id": "", "code": exc.code})
    if business_list_truncated:
        errors.append({"asset_type": "business", "business_id": "", "code": "business_pagination_limit_reached"})
    for row in businesses:
        add(_record("business", row, access_path="assigned_business"))

    direct_accounts = await edge("me/adaccounts", "ad_account", access_path="assigned_ad_account")
    direct_pages = await edge("me/accounts", "page", access_path="assigned_page")
    for page in direct_pages:
        nested = page.get("instagram_business_account")
        if isinstance(nested, dict):
            add(_record("instagram_account", nested, access_path="page_instagram_business_account"))

    known_business_ids = {str(row.get("id") or "").strip() for row in businesses}
    for account in direct_accounts:
        owner_id, owner_name = _business(account.get("business"))
        if owner_id and not assets.get(("business", owner_id)):
            add({"asset_type": "business", "external_id": owner_id, "name": owner_name,
                 "business_id": owner_id, "business_name": owner_name,
                 "access_path": "account_business", "permissions": [], "metadata": {}})
        if owner_id and owner_id not in known_business_ids:
            businesses.append({"id": owner_id, "name": owner_name or "", "_inferred_from_assigned_asset": True})
            known_business_ids.add(owner_id)

    limited_businesses = businesses[:10]
    if len(businesses) > len(limited_businesses):
        errors.append({"asset_type": "business", "business_id": "", "code": "discovery_business_limit_reached"})
    tasks = []
    semaphore = asyncio.Semaphore(4)
    for business in limited_businesses:
        business_id = str(business.get("id") or "").strip()
        if not business_id:
            continue
        business_name = str(business.get("name") or "").strip()[:240] or None
        edges = (
            ("owned_ad_accounts", "ad_account", "business_owned"),
            ("client_ad_accounts", "ad_account", "business_client"),
            ("owned_pages", "page", "business_owned"),
            ("client_pages", "page", "business_client"),
            ("owned_instagram_assets", "instagram_account", "business_owned"),
            ("client_instagram_assets", "instagram_account", "business_client"),
            ("ads_dataset", "dataset", "business_dataset"),
            ("owned_pixels", "pixel", "business_owned"),
            ("client_pixels", "pixel", "business_client"),
        )
        for suffix, asset_type, access_path in edges:
            async def load_edge(edge_suffix=suffix, kind=asset_type, origin=access_path,
                                current_business_id=business_id, current_business_name=business_name):
                async with semaphore:
                    rows = await edge(f"{current_business_id}/{edge_suffix}", kind,
                                      business_id=current_business_id,
                                      business_name=current_business_name,
                                      access_path=origin)
                if kind == "page":
                    for page in rows:
                        nested = page.get("instagram_business_account")
                        if isinstance(nested, dict):
                            add(_record("instagram_account", nested, business_id=current_business_id,
                                        business_name=current_business_name, access_path="page_instagram_business_account"))
            tasks.append(load_edge())
    if tasks:
        await asyncio.gather(*tasks)

    return {
        "assets": list(assets.values()),
        "errors": errors,
        "complete": not errors,
        "business_count": len(businesses),
        "business_limit_applied": len(businesses) > len(limited_businesses),
    }


async def revalidate_asset(token: str, asset_type: str, external_id: str, *, transport=None) -> dict[str, Any]:
    fields = ASSET_FIELDS.get(asset_type)
    if not fields:
        raise ValueError("META_ASSET_TYPE_UNSUPPORTED")
    object_id = str(external_id or "").strip()
    if not object_id or any(char in object_id for char in "/?#"):
        raise ValueError("META_ASSET_ID_INVALID")
    if asset_type == "ad_account" and not object_id.lower().startswith("act_"):
        object_id = f"act_{object_id}"
    graph = system_user_graph(token, transport=transport)
    return await graph._request("GET", object_id, params={"fields": fields})


async def validate_asset_in_business(
    token: str,
    asset_type: str,
    external_id: str,
    business_id: str,
    *,
    transport=None,
) -> dict[str, Any]:
    """Validate an ID only when the System User sees it through the selected BM."""
    if asset_type == "business":
        return await revalidate_asset(token, asset_type, external_id, transport=transport)
    edges = {
        "ad_account": ("owned_ad_accounts", "client_ad_accounts"),
        "page": ("owned_pages", "client_pages"),
        # Business returns InstagramBusinessAsset objects from these edges;
        # their stable account identifier is `ig_user_id`.
        "instagram_account": ("owned_instagram_assets", "client_instagram_assets"),
        "dataset": ("ads_dataset",),
        "pixel": ("owned_pixels", "client_pixels"),
    }
    if asset_type not in edges:
        raise ValueError("META_ASSET_TYPE_UNSUPPORTED")
    graph = system_user_graph(token, transport=transport)
    business = await graph._request("GET", str(business_id).strip(), params={"fields": "id,name"})
    if str(business.get("id") or "").strip() != str(business_id).strip():
        raise MetaGraphError("business_access_unverified", "A Meta não confirmou acesso ao portfólio selecionado.", status_code=403)
    canonical_id = str(external_id or "").strip()
    if asset_type == "ad_account" and canonical_id.lower().startswith("act_"):
        canonical_id = canonical_id[4:]
    for edge_name in edges[asset_type]:
        rows, truncated = await _list_pages(
            graph,
            f"{business_id}/{edge_name}",
            ASSET_FIELDS[asset_type],
            max_pages=5,
        )
        for row in rows:
            record = _record(
                asset_type,
                row,
                business_id=str(business.get("id")),
                business_name=str(business.get("name") or "")[:240] or None,
                access_path="business_manual_validation",
            )
            if record and record["external_id"] == canonical_id:
                record["business_access"] = [{
                    "business_id": str(business.get("id")),
                    "business_name": str(business.get("name") or "")[:240] or None,
                    "access_path": "business_manual_validation",
                }]
                return record
        if truncated:
            raise MetaGraphError(
                "asset_validation_pagination_limit",
                "A validação do ativo atingiu o limite de paginação; o vínculo não foi confirmado.",
                status_code=409,
            )
    raise MetaGraphError(
        "asset_not_assigned_to_business",
        "A Meta não retornou este ativo para o portfólio empresarial selecionado.",
        status_code=403,
    )


async def read_crm_dataset_metadata(token: str, dataset_id: str) -> dict[str, Any]:
    """Read only the CRM Dataset contract fields; never sends CAPI events."""
    clean_id = str(dataset_id or "").strip()
    if not clean_id.isdigit():
        raise MetaGraphError("invalid_object_id", "O Dataset vinculado possui um identificador inválido.")
    graph = system_user_graph(token)
    body = await graph._request(
        "GET",
        clean_id,
        params={"fields": "id,name,is_crm,is_unavailable"},
    )
    returned_id = str(body.get("id") or "").strip()
    if returned_id != clean_id:
        raise MetaGraphError("unexpected_response", "A Meta não confirmou a identidade do Dataset consultado.")
    return {
        "id": returned_id,
        "name": str(body.get("name") or "").strip()[:240] or None,
        "is_crm": body.get("is_crm") if isinstance(body.get("is_crm"), bool) else None,
        "is_unavailable": body.get("is_unavailable") if isinstance(body.get("is_unavailable"), bool) else None,
    }
