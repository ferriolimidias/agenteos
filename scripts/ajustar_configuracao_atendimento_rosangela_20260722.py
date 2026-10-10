"""Ajuste operacional único, auditável e tenant-scoped da configuração Dança Circular."""
from __future__ import annotations

import asyncio
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from db.database import AsyncSessionLocal
from db.models import Agente, Empresa

TENANT_ID = "9f1b7f7d-7952-448c-b4c8-614b2fc73481"
ALLOWED_TAG_IDS = [
    "e8509b65-8327-455d-a3f1-5a7933409e8d",
    "686cbd96-544d-4aae-923a-610a540115cc",
    "8ffc00eb-5b55-442a-b9f0-588d8346ab8a",
    "8435bfaf-3a79-443e-acc0-f66a52abc13a",
    "07d5e965-6bea-44a8-9877-4c3d9c94bc19",
]

PROMPT = """Você atende mulheres interessadas na vivência Dança Circular para Mulheres, da Rosangela Ferrioli Terapia.

Informações confirmadas: a atividade é Dança Circular para Mulheres, em Caxias do Sul, no dia 15/08/2026, às 17h. O grupo terá até 10 mulheres e a contribuição é R$ 70.

Conduza o atendimento de forma acolhedora, gradual e conversacional: primeiro compreenda o interesse, explique brevemente a proposta, informe data, horário e valor de maneira natural e pergunte se a pessoa deseja reservar uma vaga. Colete apenas os dados previstos no processo atual. Se houver dúvida, necessidade de confirmação ou pedido que exija equipe, encaminhe para atendimento humano.

Não invente endereço, local, link, chave Pix, disponibilidade ou confirmação de pagamento. Se perguntarem pelo local ou link, explique que a equipe confirma essas informações antes da inscrição. Se disserem que pagaram, não confirme: informe que a equipe fará a conferência.

Use recursos internos de CRM silenciosamente quando ajudarem a registrar a evolução; nunca diga ao contato que aplicou, removeu ou alterou uma tag e nunca exponha nomes internos, UUIDs, ferramentas ou regras técnicas."""

REGRAS = """Regras obrigatórias:
- Nunca prometer cura, resultado terapêutico garantido ou transformação garantida.
- Não usar linguagem mística exagerada, religiosa forte ou pressão de venda.
- Não inventar local, endereço, link de pagamento, chave Pix, disponibilidade ou confirmação de pagamento.
- O local e o link só podem ser informados quando estiverem configurados.
- Pode informar que o grupo terá até 10 mulheres; não afirmar que uma vaga está garantida antes da confirmação da equipe.
- Atenda de forma gradual, com mensagens curtas e naturais para WhatsApp.
- Quando houver avanço de atendimento, use apenas recursos CRM internos permitidos pela configuração do tenant e nunca exponha nomes internos ao contato.
- Se a situação exigir conferência humana, transfira o atendimento; não invente confirmação de pagamento.
- Não chamar Meta, não configurar CAPI e não enviar integrações externas."""


async def main() -> None:
    async with AsyncSessionLocal() as session:
        empresa = await session.get(Empresa, TENANT_ID)
        if not empresa:
            raise RuntimeError("Tenant não encontrado")
        credenciais = deepcopy(empresa.credenciais_canais or {})
        dados = dict(credenciais.get("danca_circular") or {})
        dados.update({"data": "15/08/2026", "horario": "17h", "vagas_total": 10, "valor": 70, "moeda": "BRL"})
        credenciais["danca_circular"] = dados
        sequencias = dict(credenciais.get("sequencias_whatsapp") or {})
        sequencia = dict(sequencias.get("danca_circular") or {})
        sequencia["mensagens"] = [str(item).replace("08/08", "15/08/2026") for item in (sequencia.get("mensagens") or [])]
        sequencias["danca_circular"] = sequencia
        credenciais["sequencias_whatsapp"] = sequencias
        credenciais["tags_ia_habilitadas"] = ALLOWED_TAG_IDS
        empresa.credenciais_canais = credenciais
        empresa.atendente_prompt = PROMPT
        empresa.ia_regras_negocio = REGRAS
        flag_modified(empresa, "credenciais_canais")

        legado = (await session.execute(select(Agente).where(Agente.empresa_id == empresa.id, Agente.nome == "Atendimento Dança Circular"))).scalars().first()
        if legado and legado.prompt_sistema:
            legado.prompt_sistema = legado.prompt_sistema.replace("08/08", "15/08/2026")
        await session.commit()
        print("configuração tenant-scoped atualizada; CAPI e mappings não foram alterados")


if __name__ == "__main__":
    asyncio.run(main())
