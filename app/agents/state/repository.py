from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.agents.schemas import ConversationState
from app.agents.state.memory import memory_from_state


CONVERSATION_STATE_KEY = "conversation_state"


def _clean_state_payload(state: ConversationState) -> dict[str, Any]:
    payload = memory_from_state(state)
    return {key: value for key, value in payload.items() if value not in (None, "", {}, [])}


class ConversationStateRepository:
    """Persistencia operacional por empresa + lead/conversa em CRMLead.dados_adicionais."""

    async def load(
        self,
        *,
        empresa_id: str | None,
        lead_id: str | None = None,
        canal: str | None = None,
        identificador: str | None = None,
    ) -> dict[str, Any]:
        async with self._session_scope() as session:
            lead = await self._find_lead(
                session,
                empresa_id=empresa_id,
                lead_id=lead_id,
                identificador=identificador,
            )
            if not lead:
                return {}
            dados = lead.dados_adicionais if isinstance(lead.dados_adicionais, dict) else {}
            state = dados.get(CONVERSATION_STATE_KEY)
            return dict(state) if isinstance(state, dict) else {}

    async def save(self, state: ConversationState) -> ConversationState:
        async with self._session_scope() as session:
            lead = await self._find_lead(
                session,
                empresa_id=state.empresa_id,
                lead_id=state.lead_id,
                identificador=state.telefone,
            )
            if not lead:
                return state
            payload = _clean_state_payload(state)
            dados = dict(lead.dados_adicionais or {})
            if payload:
                dados[CONVERSATION_STATE_KEY] = payload
            else:
                dados.pop(CONVERSATION_STATE_KEY, None)
            lead.dados_adicionais = dados
            flag_modified(lead, "dados_adicionais")
            await session.commit()
        return state

    def _session_scope(self):
        from db.database import AsyncSessionLocal

        return AsyncSessionLocal()

    async def _find_lead(self, session, *, empresa_id: str | None, lead_id: str | None, identificador: str | None):
        if not empresa_id:
            return None
        try:
            empresa_uuid = uuid.UUID(str(empresa_id))
        except (TypeError, ValueError):
            return None
        lead_uuid = None
        if lead_id:
            try:
                lead_uuid = uuid.UUID(str(lead_id))
            except (TypeError, ValueError):
                lead_uuid = None
        from db.models import CRMLead

        if lead_uuid:
            result = await session.execute(
                select(CRMLead).where(CRMLead.id == lead_uuid, CRMLead.empresa_id == empresa_uuid)
            )
        else:
            telefone = str(identificador or "").strip()
            if not telefone:
                return None
            result = await session.execute(
                select(CRMLead).where(CRMLead.empresa_id == empresa_uuid, CRMLead.telefone_contato == telefone)
            )
        return result.scalars().first()
