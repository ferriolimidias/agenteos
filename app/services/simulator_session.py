"""Tenant-scoped keys for simulator polling state."""


def simulator_response_key(empresa_id: str, sessao_id: str) -> str:
    tenant = str(empresa_id or "").strip()
    session = str(sessao_id or "").strip()
    if not tenant or not session:
        raise ValueError("empresa_id and sessao_id are required for simulator state")
    return f"sim_resp:{tenant}:{session}"
