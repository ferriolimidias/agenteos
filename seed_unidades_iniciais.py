import asyncio

from dotenv import load_dotenv
from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import Empresa, EmpresaUnidade

load_dotenv()


SEED_NOME_UNIDADE = "Matriz"
SEED_ENDERECO_PADRAO = "Endereço a definir. Por favor, atualize no painel."


async def main() -> None:
    print("[SEED UNIDADES] Iniciando seed de unidades iniciais...")

    total_empresas = 0
    total_atualizadas = 0
    total_ignoradas = 0

    async with AsyncSessionLocal() as session:
        result_empresas = await session.execute(select(Empresa))
        empresas = result_empresas.scalars().all()
        total_empresas = len(empresas)
        print(f"[SEED UNIDADES] Empresas encontradas: {total_empresas}")

        for empresa in empresas:
            result_unidades = await session.execute(
                select(EmpresaUnidade.id).where(EmpresaUnidade.empresa_id == empresa.id).limit(1)
            )
            unidade_existente = result_unidades.first()
            if unidade_existente:
                total_ignoradas += 1
                print(
                    f"[SEED UNIDADES] Empresa '{empresa.nome_empresa}' ({empresa.id}) já possui unidade. Ignorando."
                )
                continue

            nova_unidade = EmpresaUnidade(
                empresa_id=empresa.id,
                nome_unidade=SEED_NOME_UNIDADE,
                endereco_completo=SEED_ENDERECO_PADRAO,
                link_google_maps=None,
                horario_funcionamento=None,
                is_matriz=True,
            )
            session.add(nova_unidade)
            total_atualizadas += 1
            print(
                f"[SEED UNIDADES] Unidade padrão criada para empresa '{empresa.nome_empresa}' ({empresa.id})."
            )

        if total_atualizadas > 0:
            await session.commit()
            print(f"[SEED UNIDADES] Commit realizado com sucesso. Empresas atualizadas: {total_atualizadas}")
        else:
            print("[SEED UNIDADES] Nenhuma empresa precisava de seed. Nenhum commit necessário.")

    print(
        "[SEED UNIDADES] Finalizado. "
        f"Empresas analisadas: {total_empresas} | Atualizadas: {total_atualizadas} | Ignoradas: {total_ignoradas}"
    )


if __name__ == "__main__":
    asyncio.run(main())
