"""Real isolated PostgreSQL read projection with rollback-only synthetic fixtures."""
import os
from datetime import datetime, timedelta
from uuid import uuid4
import pytest
from sqlalchemy import insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from app.services.jev_shadow import read_context, read_tags, NotFound
from db.models import Empresa, CRMLead, MensagemHistorico, TagCRM, Conexao, ChannelContact, ChannelConversation, TipoConexao

URL=os.getenv('JEV_CONFIG_TEST_DATABASE_URL','')
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not URL,reason='explicit isolated PostgreSQL required')]

async def test_real_tenant_message_conversation_tags_scope_and_no_writes():
    parsed=make_url(URL)
    if parsed.host!='a00-pg' or parsed.database!='agentos_test':
        pytest.fail('refusing fixture writes outside isolated a00-pg/agentos_test')
    engine=create_async_engine(URL)
    tenant,other,lead,foreign,connection,contact,conv1,conv2=[uuid4() for _ in range(8)]
    now=datetime.utcnow()
    try:
        async with engine.connect() as conn:
            transaction=await conn.begin()
            try:
                for id in (tenant,other):await conn.execute(insert(Empresa.__table__).values(id=id,nome_empresa='Synthetic JEV context test',credenciais_canais={}))
                await conn.execute(insert(CRMLead.__table__).values(id=lead,empresa_id=tenant,nome_contato='Synthetic contact',tags=[]))
                await conn.execute(insert(CRMLead.__table__).values(id=foreign,empresa_id=other,nome_contato='Synthetic foreign',tags=[]))
                await conn.execute(insert(Conexao.__table__).values(id=connection,empresa_id=tenant,tipo=list(TipoConexao)[0],nome_instancia='JEV-synthetic'))
                await conn.execute(insert(ChannelContact.__table__).values(id=contact,empresa_id=tenant,conexao_id=connection,lead_id=lead,channel='whatsapp',provider='evolution',external_contact_id='synthetic'))
                for id in (conv1,conv2):
                    await conn.execute(insert(ChannelConversation.__table__).values(id=id,empresa_id=tenant,conexao_id=connection,contact_id=contact,lead_id=lead,channel='whatsapp',provider='evolution',conversation_key=str(id),external_conversation_id=str(id)))
                for index in range(14):
                    await conn.execute(insert(MensagemHistorico.__table__).values(id=uuid4(),empresa_id=tenant,lead_id=lead,channel_conversation_id=conv2,
                        texto=f'Synthetic recent {index} preço',tipo_mensagem='text',from_me=index%2==0,direction='outbound' if index%2==0 else 'inbound',criado_em=now+timedelta(seconds=index)))
                await conn.execute(insert(MensagemHistorico.__table__).values(id=uuid4(),empresa_id=tenant,lead_id=lead,channel_conversation_id=conv1,texto='MUST_NOT_MIX_OTHER_CONVERSATION',tipo_mensagem='text',from_me=False,direction='inbound',criado_em=now-timedelta(seconds=10)))
                await conn.execute(insert(MensagemHistorico.__table__).values(id=uuid4(),empresa_id=other,lead_id=foreign,texto='FOREIGN_MESSAGE',tipo_mensagem='text',from_me=False,criado_em=now+timedelta(seconds=999)))
                await conn.execute(insert(MensagemHistorico.__table__).values(id=uuid4(),empresa_id=tenant,lead_id=lead,channel_conversation_id=conv1,texto='UNCLASSIFIABLE_DIRECTION',tipo_mensagem='text',from_me=False,direction='unknown',criado_em=now+timedelta(seconds=999)))
                own_tag=uuid4();foreign_tag=uuid4()
                for id,tid in ((own_tag,tenant),(foreign_tag,other)):
                    await conn.execute(insert(TagCRM.__table__).values(id=id,empresa_id=tid,nome='Preço',cor='#123456'))
                async with AsyncSession(bind=conn) as db:
                    messages=await read_context(db,tenant,lead)
                    assert len(messages)==8 and {m['role'] for m in messages}=={'agent','customer'}
                    assert all(m['text'].startswith('Synthetic recent') for m in messages)
                    assert messages[0]['text']=='Synthetic recent 6 preço' and messages[-1]['text']=='Synthetic recent 13 preço'
                    with pytest.raises(NotFound):await read_context(db,tenant,foreign)
                    with pytest.raises(NotFound):await read_context(db,other,lead)
                    tags,_=await read_tags(db,tenant)
                    assert [t['id'] for t in tags]==[str(own_tag)]
            finally:
                await transaction.rollback()
    finally:await engine.dispose()
