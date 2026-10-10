"""Small, explicit HTTP policy catalog used by the route contract test."""

from __future__ import annotations

import re

from fastapi.routing import APIRoute


# Every public route is named here. Parameterized paths match FastAPI's
# registered route templates exactly; new endpoints do not inherit public access.
PUBLIC_ROUTE_ALLOWLIST: dict[tuple[str, str], str] = {
    ("GET", "/"): "Liveness probe; returns no tenant or operational data.",
    ("GET", "/openapi.json"): "Public API schema documentation.",
    ("GET", "/docs"): "Public API documentation UI.",
    ("GET", "/docs/oauth2-redirect"): "Swagger OAuth documentation callback.",
    ("GET", "/redoc"): "Public API documentation UI.",
    ("HEAD", "/openapi.json"): "Read-only API schema metadata.",
    ("HEAD", "/docs"): "Read-only API documentation UI metadata.",
    ("HEAD", "/docs/oauth2-redirect"): "Read-only Swagger documentation callback metadata.",
    ("HEAD", "/redoc"): "Read-only API documentation UI metadata.",
    ("POST", "/api/auth/login"): "Credential exchange endpoint.",
    ("GET", "/api/public/configuracoes-branding"): "Non-sensitive public branding.",
    ("GET", "/api/public/anamnese/{slug}/session"): "Opaque tokenized customer form.",
    ("POST", "/api/public/anamnese/{slug}/respostas"): "Opaque tokenized customer form.",
    ("POST", "/api/public/anamnese/{slug}/event"): "Opaque tokenized customer form event.",
    ("GET", "/api/public/leoft-app/access/session"): "Student access token flow.",
    ("GET", "/api/public/leoft-app/workouts"): "Student access token flow.",
    ("GET", "/api/public/leoft-app/workouts/{assignment_id}"): "Student access token flow.",
    ("GET", "/api/public/leoft-app/plan"): "Student access token flow.",
    ("GET", "/api/public/leoft-app/progress"): "Student access token flow.",
    ("POST", "/api/public/leoft-app/progress/exercises/{exercise_id}/complete"): "Student access token flow.",
    ("DELETE", "/api/public/leoft-app/progress/exercises/{exercise_id}/complete"): "Student access token flow.",
    ("POST", "/api/public/leoft-app/progress/workouts/{workout_id}/complete"): "Student access token flow.",
    ("DELETE", "/api/public/leoft-app/progress/workouts/{workout_id}/complete"): "Student access token flow.",
    ("GET", "/api/public/leoft-training/approve"): "Single-use tokenized approval callback.",
    ("GET", "/api/webhook/meta"): "Meta verification token challenge.",
    ("POST", "/api/webhook/meta"): "Meta provider callback with HMAC verification.",
    ("GET", "/api/webhooks/meta/instagram"): "Meta verification token challenge.",
    ("POST", "/api/webhooks/meta/instagram"): "Instagram provider callback with HMAC verification.",
    ("POST", "/api/webhooks/mercado-pago/leoft"): "Payment provider callback with signature verification.",
    ("POST", "/api/webhooks/mercado-pago/{empresa_id}"): "Payment provider callback with tenant signature verification.",
    ("POST", "/api/webhook/{empresa_id}/meta"): "Legacy Meta provider callback; HMAC dependency required.",
    ("POST", "/api/webhook/{empresa_id}/evolution"): "Evolution callback; instance token dependency required.",
    ("POST", "/api/webhook/{empresa_id}/telegram"): "Telegram callback; tenant token dependency required.",
    ("POST", "/api/webhook/{empresa_id}/chatwoot"): "Chatwoot callback; tenant token dependency required.",
}

AUTH_DEPENDENCIES = {
    "get_current_user",
    "require_tenant_access",
    "require_super_admin",
    "require_ia_config_access",
}

PROVIDER_DEPENDENCIES = {
    "require_legacy_meta_signature",
    "require_evolution_webhook_token",
    "require_telegram_webhook_token",
    "require_chatwoot_webhook_token",
}

INTERNAL_HMAC_DEPENDENCIES = {
    "_auth",
    "_catalog_auth",
    "require_hermes_catalog_read_hmac",
    "require_hermes_generation_hmac",
}
RESOURCE_ID_PATH_PARAMETER = re.compile(r"\{[^{}]*id[^{}]*\}", re.IGNORECASE)

# These resource routes intentionally do not put empresa_id in the URL. Their
# handlers authenticate the user and add the user's tenant to the SQL query
# before loading the connection. Keep that policy explicit in the contract.
TENANT_RESOURCE_READ_ROUTES = {
    "/api/conexoes/{conexao_id}/status",
    "/api/conexoes/status/{conexao_id}",
    "/api/conexoes/{conexao_id}/qrcode",
}


def dependency_names(dependant) -> set[str]:
    names: set[str] = set()
    call = getattr(dependant, "call", None)
    if call is not None:
        names.add(getattr(call, "__name__", ""))
    for child in getattr(dependant, "dependencies", []) or []:
        names.update(dependency_names(child))
    return names


def policy_for_route(route: APIRoute, method: str) -> str | None:
    method = method.upper()
    route_path = str(getattr(route, "path", ""))
    names = dependency_names(getattr(route, "dependant", None))
    if not isinstance(route, APIRoute):
        return "PUBLIC_INTENTIONAL" if (method, route_path) in PUBLIC_ROUTE_ALLOWLIST else None
    if route_path in TENANT_RESOURCE_READ_ROUTES and "get_current_user" in names:
        return "TENANT_READ"
    if names & AUTH_DEPENDENCIES:
        if "require_super_admin" in names:
            return "SUPER_ADMIN_ONLY"
        if "require_tenant_access" in names or "require_ia_config_access" in names:
            return "TENANT_READ" if method in {"GET", "HEAD"} else "TENANT_WRITE"
        # A plain JWT proves identity, not membership in the tenant named by a
        # company-scoped URL. Such routes must use the tenant dependency (or a
        # deliberately mapped resource-ownership policy above).
        if route_path.startswith("/api/empresas/") or RESOURCE_ID_PATH_PARAMETER.search(route_path):
            return None
        return "AUTHENTICATED_GLOBAL"

    if names & PROVIDER_DEPENDENCIES:
        return "WEBHOOK_PROVIDER"

    if route_path.startswith("/api/internal/hermes/"):
        if names & INTERNAL_HMAC_DEPENDENCIES:
            return "INTERNAL_SERVICE"
        # Generation endpoints build a dependency factory per event/scope. The
        # closure is registered in the router only after applying that HMAC.
        if (
            route.endpoint.__module__ == "app.api.routers.leoft_generation_internal"
            and "dependency" in names
        ):
            return "INTERNAL_SERVICE"
        return None

    if (method, route_path) in PUBLIC_ROUTE_ALLOWLIST:
        return "PUBLIC_INTENTIONAL"
    return None
