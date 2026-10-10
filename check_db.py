import asyncio
from db.database import engine
from sqlalchemy import text

async def check():
    async with engine.connect() as conn:
        res1 = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='empresas'"))
        cols1 = [r[0] for r in res1]
        print('empresas cols:', cols1)
        if 'modelo_roteador' not in cols1:
            print("Adicionando modelo_roteador em empresas")
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN modelo_roteador VARCHAR DEFAULT 'gpt-4o-mini'"))
            await conn.commit()
            
        res2 = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='especialistas'"))
        cols2 = [r[0] for r in res2]
        print('especialistas cols:', cols2)
        if 'modelo_ia' not in cols2:
            print("Adicionando modelo_ia em especialistas")
            await conn.execute(text("ALTER TABLE especialistas ADD COLUMN modelo_ia VARCHAR DEFAULT 'gpt-4o-mini'"))
            await conn.commit()
            
        print("Done")

asyncio.run(check())
