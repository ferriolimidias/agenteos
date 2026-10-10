import asyncio
from sqlalchemy import update
from db.database import AsyncSessionLocal
from db.models import Especialista


async def main():
    async with AsyncSessionLocal() as session:
        # Atualiza a descrição de roteamento para incluir palavras sobre pontos de referência
        nova_descricao = "endereço, localização, onde fica, mapa, google maps, matriz, filial, ponto de referência, como chegar, referências do local, fica perto de onde, rua, avenida"

        await session.execute(
            update(Especialista)
            .where(Especialista.nome == 'especialista_localizacao')
            .values(descricao_roteamento=nova_descricao)
        )
        await session.commit()
        print("✅ Vocabulário do Especialista de Localização atualizado com sucesso!")


if __name__ == "__main__":
    asyncio.run(main())
