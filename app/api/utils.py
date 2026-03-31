import asyncio
import os
import traceback
import uuid
from typing import List
from app.schemas import StandardMessage
from app.services.websocket_manager import manager

LOG_LEVEL_CONVERSATION = os.getenv("LOG_LEVEL_CONVERSATION", "INFO").upper()


def _conversation_debug_enabled() -> bool:
    return LOG_LEVEL_CONVERSATION == "DEBUG"


def _conversation_debug_log(message: str) -> None:
    if _conversation_debug_enabled():
        print(message)


def get_llm_model(model_name: str, api_key: str = None):
    """
    Fábrica de LLM para retornar o objeto correto (OpenAI, Google ou Anthropic) baseando-se no nome do modelo.
    """
    model_lower = model_name.lower() if model_name else "gpt-4o-mini"
    
    print(f"[get_llm_model] Solicitado modelo: '{model_name}' (interpretado: '{model_lower}')")
    
    # Alguns modelos (como o- series) não suportam temperature ou exigem config especial
    temperature = 0.7
    if model_lower.startswith("o"):
        temperature = 1.0

    openai_model_kwargs = {"frequency_penalty": 0.4, "presence_penalty": 0.4}

    if model_lower.startswith("gemini-"):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError("Instale langchain-google-genai para usar modelos Gemini.")
        import os
        key = api_key or os.environ.get("GOOGLE_API_KEY")
        print(f"[get_llm_model] Instanciando ChatGoogleGenerativeAI para '{model_lower}'")
        return ChatGoogleGenerativeAI(model=model_name, temperature=temperature, google_api_key=key)
    
    elif model_lower.startswith("claude-"):
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("Instale langchain-anthropic para usar modelos Claude.")
        import os
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        print(f"[get_llm_model] Instanciando ChatAnthropic para '{model_lower}'")
        return ChatAnthropic(model=model_name, temperature=temperature, anthropic_api_key=key)
        
    elif model_lower.startswith("gpt-") or model_lower.startswith("o"):
        from langchain_openai import ChatOpenAI
        import os
        key = api_key or os.environ.get("OPENAI_API_KEY")

        print(f"[get_llm_model] Instanciando ChatOpenAI para '{model_lower}'")
        if model_lower.startswith("o"):
            if key:
                return ChatOpenAI(model=model_name, api_key=key, model_kwargs=openai_model_kwargs)
            return ChatOpenAI(model=model_name, model_kwargs=openai_model_kwargs)
        else:
            if key:
                return ChatOpenAI(
                    model=model_name,
                    temperature=temperature,
                    api_key=key,
                    model_kwargs=openai_model_kwargs,
                )
            return ChatOpenAI(
                model=model_name,
                temperature=temperature,
                model_kwargs=openai_model_kwargs,
            )
    
    else:
        print(f"[get_llm_model] NENHUM prefixo conhecido encontrado para '{model_name}'. FALLBACK OBRIGATÓRIO para 'gpt-4o-mini'")
        from langchain_openai import ChatOpenAI
        import os
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if key:
            return ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.7,
                api_key=key,
                model_kwargs=openai_model_kwargs,
            )
        return ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            model_kwargs=openai_model_kwargs,
        )

import json
from datetime import timedelta

async def get_available_models() -> List[str]:
    """
    Lista modelos disponíveis filtrando apenas os de Chat das principais APIs.
    Realiza cache da lista no Redis por 24 horas para evitar lentidão.
    """
    from app.api.main import redis_client
    cache_key = "available_ai_models"
    
    # Tenta buscar do cache primeiro
    cached = await redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
        
    modelos = []
    
    # 1. OpenAI Models
    try:
        from openai import AsyncOpenAI
        import os
        client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = await client.models.list()
        
        # Filtros básicos para OpenAI: gpt-4, gpt-5, o1, o3, o4. Ignorar vision antigos, instruct, embedding.
        for m in response.data:
            m_name = m.id.lower()
            if any(k in m_name for k in ["gpt-4", "gpt-5", "o1", "o3", "o4"]):
                if "vision" not in m_name and "instruct" not in m_name and "audio" not in m_name and "realtime" not in m_name:
                    modelos.append(m.id)
    except Exception as e:
        print(f"Aviso: Não foi possível listar modelos OpenAI: {e}")
        # Fallback básico caso api key falhe
        modelos.extend(["gpt-4o", "gpt-4o-mini"])

    # 2. Google (Gemini) - Hardcoded pois Google API list models varia muito
    modelos.extend(["gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.0-pro"])
    
    # 3. Anthropic (Claude) - Hardcoded pois não tem endpoint padrão list models aberto na mesma key
    modelos.extend(["claude-3-5-sonnet-20240620", "claude-3-opus-20240229", "claude-3-haiku-20240307"])
    
    # Dedup and sort
    modelos = sorted(list(set(modelos)))
    
    # Salva no cache por 24 horas (86400 segundos)
    await redis_client.setex(cache_key, 86400, json.dumps(modelos))
    
    return modelos


async def _salvar_historico_saida_ia(
    *,
    empresa_id: str,
    telefone: str,
    resposta: str,
    conexao_id: str | None,
) -> dict | None:
    """
    Persiste a mensagem outbound da IA em uma sessão isolada/limpa.
    Evita reaproveitar transações potencialmente sujas do fluxo principal.
    """
    from db.database import AsyncSessionLocal
    from db.models import CRMLead, MensagemHistorico
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        empresa_uuid = uuid.UUID(empresa_id)
        conexao_id_limpo = str(conexao_id or "").strip()
        try:
            conexao_uuid = uuid.UUID(conexao_id_limpo) if conexao_id_limpo else None
        except (ValueError, TypeError):
            print(
                "[ENGINE] conexao_id inválido ao salvar histórico da IA; "
                f"valor recebido='{conexao_id_limpo}'. Salvando com conexao_id=None."
            )
            conexao_uuid = None

        result = await session.execute(
            select(CRMLead).where(
                CRMLead.empresa_id == empresa_uuid,
                CRMLead.telefone_contato == telefone,
            )
        )
        lead = result.scalars().first()
        if not lead:
            print(
                "[ENGINE] Lead não encontrado ao salvar histórico outbound da IA. "
                f"empresa_id={empresa_id} telefone='{telefone}'"
            )
            return None

        nova_msg = MensagemHistorico(
            lead_id=lead.id,
            conexao_id=conexao_uuid,
            texto=resposta,
            from_me=True,
        )

        try:
            session.add(nova_msg)
            await session.commit()
            await session.refresh(nova_msg)
        except Exception as e:
            print(f"ERRO CRÍTICO NO BANCO AO SALVAR HISTÓRICO DA IA: {str(e)}")
            traceback.print_exc()
            await session.rollback()
            return None

        return {
            "id": str(nova_msg.id),
            "texto": str(nova_msg.texto or ""),
            "from_me": bool(nova_msg.from_me),
            "tipo_mensagem": str(nova_msg.tipo_mensagem or "text"),
            "media_url": str(nova_msg.media_url) if nova_msg.media_url else None,
            "criado_em": nova_msg.criado_em.isoformat() if nova_msg.criado_em else None,
        }

async def processar_bloco_mensagens(mensagens: List[StandardMessage]):
    """
    Função assíncrona que processa o bloco de mensagens quando o timer do debouncer termina.
    Importa e invoca o Grafo do LangGraph passando o bloco de texto e o identificador.
    """
    if not mensagens:
        return
        
    from app.core.agent_graph import graph
    from app.api.main import redis_client
        
    print(f"\n--- INICIANDO PROCESSAMENTO IA ---")
    print(f"Canal: {mensagens[0].canal} | Origem: {mensagens[0].identificador_origem} | Empresa: {mensagens[0].empresa_id}")
    print(f"Total de mensagens no bloco original: {len(mensagens)}")
    
    # 1. Block de Mensagem Vazia: Filtra e remove mensagens vazias
    mensagens_validas = []
    for msg in mensagens:
        texto_limpo = msg.texto_mensagem.strip()
        if texto_limpo:
            msg.texto_mensagem = texto_limpo
            mensagens_validas.append(msg)
            
    if not mensagens_validas:
        print("[ENGINE] Bloco de mensagens resultou vazio após limpeza. Abortando LangGraph.")
        return

    if _conversation_debug_enabled():
        for i, msg in enumerate(mensagens_validas, 1):
            remetente = "Humano" if msg.is_human_agent else "Usuário/Lead"
            print(f" [{i}] {remetente}: {msg.texto_mensagem}")
        
    textos = [msg.texto_mensagem for msg in mensagens_validas]
    
    # ── PONTO 1: Ingestão do histórico real do PostgreSQL ──────────────────────
    historico_bd_formatado = ""
    mensagens_globais = list(textos)
    try:
        import uuid as _uuid
        from db.database import AsyncSessionLocal as _ASL
        from db.models import CRMLead as _CRMLead, MensagemHistorico as _MH
        from sqlalchemy import select as _select

        empresa_id_hist = mensagens[0].empresa_id
        telefone_hist   = str(mensagens[0].identificador_origem)
        empresa_uuid_hist = _uuid.UUID(empresa_id_hist)

        async with _ASL() as _sess:
            # Encontra o lead pelo telefone + empresa
            _res_lead = await _sess.execute(
                _select(_CRMLead).where(
                    _CRMLead.empresa_id    == empresa_uuid_hist,
                    _CRMLead.telefone_contato == telefone_hist
                )
            )
            _lead_hist = _res_lead.scalars().first()

            if _lead_hist:
                # Busca as últimas 15 mensagens, da mais antiga para a mais nova
                _res_hist = await _sess.execute(
                    _select(_MH)
                    .where(_MH.lead_id == _lead_hist.id)
                    .order_by(_MH.criado_em.desc())
                    .limit(15)
                )
                _msgs_hist = list(reversed(_res_hist.scalars().all()))

                if _msgs_hist:
                    linhas = []
                    for _m in _msgs_hist:
                        papel = "Assistente" if _m.from_me else "Usuario"
                        linhas.append(f"{papel}: {_m.texto}")
                    historico_bd_formatado = "\n".join(linhas)
                    mensagens_globais = list(linhas)
                    print(f"[ENGINE] Histórico PostgreSQL carregado: {len(_msgs_hist)} mensagem(ns).")
                    _conversation_debug_log(f"[ENGINE][DEBUG] Histórico formatado:\n{historico_bd_formatado}")
                else:
                    print("[ENGINE] Lead encontrado, sem histórico anterior no banco.")
            else:
                print("[ENGINE] Lead não encontrado ainda — histórico vazio.")
    except Exception as _e_hist:
        print(f"[ENGINE] Aviso: falha ao carregar histórico do Postgres: {_e_hist}")
    # ────────────────────────────────────────────────────────────────────────────

    estado_inicial = {
        "empresa_id": mensagens[0].empresa_id,
        "identificador_origem": mensagens[0].identificador_origem,
        "canal": mensagens[0].canal,
        "conexao_id": mensagens[0].conexao_id,
        "mensagens": mensagens_globais,
        "historico_bd": historico_bd_formatado,
        "nome_contato": getattr(mensagens[0], "nome_contato", None),
        "intencao": [],
        "especialistas_selecionados": [],
        "super_contexto_especialistas": "",
        "respostas_especialistas": [],
        "acoes_sistema_pendentes": [],
        "acoes_sistema_executadas": [],
        "acoes_sistema_status": [],
        "handoff_requested": False,
        "resposta_final": None,
        "status_conversa": None
    }
    
    import time
    timestamp_inicio = await redis_client.get(f"last_msg_time:{mensagens[0].canal}:{mensagens[0].identificador_origem}")
    in_flight_key = f"inflight:{mensagens[0].canal}:{mensagens[0].identificador_origem}"
    
    # Verificacao de 'In-Flight'
    lock_adquirido = await redis_client.setnx(in_flight_key, "1")
    if not lock_adquirido:
        print(f"\n[ENGINE] Descartando resposta obsoleta para Lead {mensagens[0].identificador_origem} - Processamento mais novo em curso.")
        return
        
    await redis_client.expire(in_flight_key, 30) # Lock morre em 30s de qualquer jeito
    
    try:
        print("\n[LangGraph] Invocando Grafo...")
        thread_id = f"{mensagens[0].empresa_id}:{mensagens[0].canal}:{mensagens[0].identificador_origem}"
        config = {"configurable": {"thread_id": thread_id}}
        estado_final = await graph.ainvoke(estado_inicial, config=config)
        
        _conversation_debug_log("\n--- ESTADO FINAL DA IA ---")
        _conversation_debug_log(str(estado_final))
        _conversation_debug_log("--------------------------------------\n")
        
        # Timestamp Lock - Verifica se chegou mensagem BEM na hora que o Langgraph pensava
        timestamp_fim = await redis_client.get(f"last_msg_time:{mensagens[0].canal}:{mensagens[0].identificador_origem}")
        
        if timestamp_inicio and timestamp_fim and timestamp_inicio != timestamp_fim:
             print(f"\n[ENGINE] Descartando resposta gerada para Lead {mensagens[0].identificador_origem} - Nova mensagem chegou durante a geracao (Timestamp mismatch).")
             return
    except Exception as e:
        print(f"\n[ENGINE] Erro no processamento do LangGraph: {e}")
        return
    finally:
        await redis_client.delete(in_flight_key)
        
    print("--------------------------------------\n")
    
    from app.services.channel_factory import despachar_mensagem
    
    conexao_id_dispatch = estado_final.get("conexao_id") or mensagens[0].conexao_id
    resposta = estado_final.get("resposta_final")
    if resposta:
        print("\n[Channel Factory] Despachando a resposta final...")
        
        if mensagens[0].canal == "simulador":
            # Para o simulador, escrever a resposta no Redis para o long-poll do frontend
            sim_key = f"sim_resp:{mensagens[0].identificador_origem}"
            await redis_client.setex(sim_key, 120, resposta)  # TTL de 2 minutos
            print(f"[SIMULADOR] Resposta gravada no Redis: {sim_key}")
            
            # Salvar histórico mock no banco
            try:
                textos_juntos = "\n".join(textos)
                await save_simulator_history(
                    mensagens[0].empresa_id,
                    mensagens[0].identificador_origem,
                    textos_juntos,
                    resposta,
                    mensagens[0].conexao_id,
                )
            except Exception as e:
                print(f"[SIMULADOR] Aviso: falha ao salvar histórico simulador: {e}")
        else:
            try:
                enviado = await despachar_mensagem(
                    canal=mensagens[0].canal,
                    identificador_origem=mensagens[0].identificador_origem,
                    texto=resposta,
                    conexao_id=conexao_id_dispatch,
                    empresa_id=mensagens[0].empresa_id,
                )
            except Exception as e:
                print(
                    f"[ENGINE] Falha no envio outbound da IA. "
                    f"empresa_id={mensagens[0].empresa_id} canal={mensagens[0].canal} "
                    f"identificador='{mensagens[0].identificador_origem}' conexao_id={conexao_id_dispatch}"
                )
                print(f"Erro real no outbound: {str(e)}")
                traceback.print_exc()
                return

            if not enviado:
                print(
                    f"[ENGINE] Falha no envio outbound da IA. "
                    f"empresa_id={mensagens[0].empresa_id} canal={mensagens[0].canal} "
                    f"identificador='{mensagens[0].identificador_origem}' conexao_id={conexao_id_dispatch}"
                )
                return

            try:
                # 3. Trava de Transacao do Banco (O Check Final)
                # Re-verificar timestamp ANTES de commitar no banco
                timestamp_finalissimo = await redis_client.get(f"last_msg_time:{mensagens[0].canal}:{mensagens[0].identificador_origem}")
                if timestamp_inicio and timestamp_finalissimo and timestamp_inicio != timestamp_finalissimo:
                     print(f"\n[ENGINE] ATENÇÃO: Descartando commit no banco para Lead {mensagens[0].identificador_origem} - Nova mensagem chegou no último segundo (Timestamp mismatch).")
                     return

                telefone = str(mensagens[0].identificador_origem)
                mensagem_payload = await _salvar_historico_saida_ia(
                    empresa_id=mensagens[0].empresa_id,
                    telefone=telefone,
                    resposta=resposta,
                    conexao_id=conexao_id_dispatch or mensagens[0].conexao_id,
                )
                if mensagem_payload:
                    await manager.broadcast_to_empresa(
                        mensagens[0].empresa_id,
                        {
                            "tipo_evento": "nova_mensagem_outbound",
                            "telefone": telefone,
                            "mensagem": mensagem_payload,
                        },
                    )
            except Exception as e:
                print(f"Erro ao salvar histórico do Grafo (Webhook): {e}")
                traceback.print_exc()
            
    else:
        print("\n[Aviso] Nenhuma 'resposta_final' gerada pelo grafo.")

    # ── Motor de Follow-up Resiliente ─────────────────────────────────────────
    # Estratégia:
    #   followup_ativo:{canal}:{origem} → token único (guardião do job)
    #   followup_nivel:{canal}:{origem} → "1" ou "2" (memória de estado)
    #
    # Quando o lead responde → ambas as chaves são DELETADAS → a task acorda,
    # verifica que a chave não existe e ABORTA. Sem race condition.
    # ──────────────────────────────────────────────────────────────────────────
    status_conv = estado_final.get("status_conversa", "ABERTA")
    if status_conv == "ABERTA":
        import time as _time_mod
        canal_orig     = mensagens[0].canal
        id_orig        = mensagens[0].identificador_origem
        empresa_id_fu  = mensagens[0].empresa_id
        job_token      = str(_time_mod.time())

        ativo_key  = f"followup_ativo:{canal_orig}:{id_orig}"
        nivel_key  = f"followup_nivel:{canal_orig}:{id_orig}"
        
        # ── BUSCA CONFIGURAÇÃO DINÂMICA DA EMPRESA ──────────────────────────
        from db.database import AsyncSessionLocal as _ASL
        from db.models import Empresa as _Empresa
        from sqlalchemy import select as _sel
        import uuid as _uuid_mod

        try:
            async with _ASL() as sess_fu:
                emp_uuid = _uuid_mod.UUID(empresa_id_fu)
                res_emp = await sess_fu.execute(_sel(_Empresa).where(_Empresa.id == emp_uuid))
                emp_fu = res_emp.scalars().first()

                if not emp_fu or not getattr(emp_fu, 'followup_ativo', False):
                    print(f"[Follow-up] ⏩ Ignorado — Follow-up desativado nas configurações da empresa (ID: {empresa_id_fu}).")
                    return

                delay_n1 = (getattr(emp_fu, 'followup_espera_nivel_1_minutos', 20) or 20) * 60
                delay_n2 = (getattr(emp_fu, 'followup_espera_nivel_2_minutos', 10) or 10) * 60
                print(f"[Follow-up] Configurações carregadas: Ativo=Sim | N1={delay_n1}s | N2={delay_n2}s")
        except Exception as e_cfg:
            print(f"[Follow-up] Erro ao carregar configurações (usando defaults): {e_cfg}")
            delay_n1 = 1200 # 20min default
            delay_n2 = 600  # 10min default
        # ──────────────────────────────────────────────────────────────────

        print(f"\n[Follow-up] Status 'ABERTA'. Agendando Nível 1 em {delay_n1}s para {ativo_key}...")

        # Grava o token de vida e o nível inicial
        await redis_client.setex(ativo_key, delay_n1 + 60, job_token)   # TTL ligeiramente maior que o delay
        await redis_client.setex(nivel_key, delay_n1 + 120, "1")

        async def _job_followup(delay: int, nivel: int):
            await asyncio.sleep(delay)

            # Verificação de vida: a chave ativo ainda existe com o MESMO token?
            token_atual = await redis_client.get(ativo_key)
            if token_atual is None:
                print(f"\n[Follow-up] ⛔ ABORTADO — chave '{ativo_key}' não existe. Lead respondeu. Nenhuma mensagem enviada.")
                return
            if token_atual != job_token:
                print(f"\n[Follow-up] ⛔ ABORTADO — token divergente. Um novo ciclo assumiu. Nenhuma mensagem enviada.")
                return

            print(f"\n{'='*50}")
            print(f"⏰ [REENGAJAMENTO PROATIVO NÍVEL {nivel} INICIADO] — Canal={canal_orig} | Origem={id_orig}")

            try:
                from app.services.channel_factory import despachar_mensagem
                from app.core.agent_graph import gerar_followup_contextual, gerar_followup_encerramento

                if nivel == 1:
                    texto = await gerar_followup_contextual(canal_orig, id_orig, empresa_id_fu)
                    _conversation_debug_log(f"[Follow-up Nível 1] Texto gerado: '{texto}'")

                    await despachar_mensagem(
                        canal=canal_orig,
                        identificador_origem=id_orig,
                        texto=texto,
                        conexao_id=conexao_id_dispatch,
                        empresa_id=empresa_id_fu,
                    )

                    novo_token = str(_time_mod.time() + 1)
                    await redis_client.setex(ativo_key, delay_n2 + 60, novo_token)
                    await redis_client.setex(nivel_key, delay_n2 + 120, "2")
                    print(f"[Follow-up] Nível 2 agendado em {delay_n2}s.", flush=True)

                    # Cria nova task para o Nível 2 com o novo token capturado em closure
                    async def _job_n2(delay_inner: int, token_inner: str):
                        await asyncio.sleep(delay_inner)
                        token_check = await redis_client.get(ativo_key)
                        if token_check is None:
                            print(f"\n[Follow-up] ⛔ N2 ABORTADO — lead respondeu antes do encerramento.")
                            return
                        if token_check != token_inner:
                            print(f"\n[Follow-up] ⛔ N2 ABORTADO — token divergente.")
                            return

                        print(f"\n{'='*50}")
                        print(f"⏰ [REENGAJAMENTO PROATIVO NÍVEL 2 INICIADO] — Canal={canal_orig} | Origem={id_orig}")
                        try:
                            texto_enc = await gerar_followup_encerramento(canal_orig, id_orig, empresa_id_fu)
                            _conversation_debug_log(f"[Follow-up Nível 2] Texto gerado: '{texto_enc}'")
                            await despachar_mensagem(
                                canal=canal_orig,
                                identificador_origem=id_orig,
                                texto=texto_enc,
                                conexao_id=conexao_id_dispatch,
                                empresa_id=empresa_id_fu,
                            )
                        except Exception as e_n2:
                            print(f"[Follow-up Nível 2] Erro: {e_n2}")
                        finally:
                            await redis_client.delete(ativo_key)
                            await redis_client.delete(nivel_key)
                        print("="*50 + "\n")

                    asyncio.create_task(_job_n2(delay_n2, novo_token))

                elif nivel == 2:
                    # Fallback seguro (caso a task de N2 fosse iniciada via outro caminho)
                    texto_enc = await gerar_followup_encerramento(canal_orig, id_orig, empresa_id_fu)
                    _conversation_debug_log(f"[Follow-up Nível 2 — fallback] Texto: '{texto_enc}'")
                    await despachar_mensagem(
                        canal=canal_orig,
                        identificador_origem=id_orig,
                        texto=texto_enc,
                        conexao_id=conexao_id_dispatch,
                        empresa_id=empresa_id_fu,
                    )
                    await redis_client.delete(ativo_key)
                    await redis_client.delete(nivel_key)

            except Exception as e:
                print(f"[Follow-up Nível {nivel}] Erro inesperado: {e}")

            print("="*50 + "\n")

        asyncio.create_task(_job_followup(delay_n1, 1))

    else:
        print(f"\n[Follow-up] Status '{status_conv}'. Deletando chaves de follow-up...")
        canal_orig = mensagens[0].canal
        id_orig    = mensagens[0].identificador_origem
        await redis_client.delete(f"followup_ativo:{canal_orig}:{id_orig}")
        await redis_client.delete(f"followup_nivel:{canal_orig}:{id_orig}")
    # ──────────────────────────────────────────────────────────────────────────




async def handle_debouncer(msg: StandardMessage):
    """
    Lógica principal do Debouncer baseada em Redis Sliding Window com Atomic Rename.
    """
    from app.api.main import redis_client
    import time
    import asyncio
    
    lead_id = f"{msg.canal}:{msg.identificador_origem}"
    
    # 1. Chegada
    await redis_client.rpush(f"queue:{lead_id}", msg.model_dump_json())
    agora = time.time()
    await redis_client.set(f"last_msg:{lead_id}", agora)
    
    # 2. Espera (Debounce)
    await asyncio.sleep(6)
    
    # 3. Verificação de Sobrevivência
    ultimo_registro = await redis_client.get(f"last_msg:{lead_id}")
    if ultimo_registro and float(ultimo_registro) > agora:
        return
        
    # 4. Lock de Execução (Single Winner)
    lock = await redis_client.set(f"lock:{lead_id}", "1", nx=True, ex=30)
    if not lock:
        return
        
    # 5. Atomic Rename (Pulo do Gato)
    batch_key = f"processando:{lead_id}:{agora}"
    try:
        await redis_client.rename(f"queue:{lead_id}", batch_key)
    except Exception:
        await redis_client.delete(f"lock:{lead_id}")
        return
        
    # 6. Processamento e Limpeza
    try:
        mensagens_json = await redis_client.lrange(batch_key, 0, -1)
        mensagens = [StandardMessage.model_validate_json(m) for m in mensagens_json]
        if mensagens:
            await processar_bloco_mensagens(mensagens)
    finally:
        await redis_client.delete(batch_key)
        await redis_client.delete(f"lock:{lead_id}")

async def get_orchestrator_system_prompt(empresa_id: str | None, is_primeira_mensagem: bool = False) -> str:
    from db.database import AsyncSessionLocal
    from sqlalchemy.future import select
    from db.models import Empresa
    import uuid

    context_xml = ""
    if empresa_id:
        try:
            emp_uuid = uuid.UUID(str(empresa_id))
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Empresa).where(Empresa.id == emp_uuid))
                empresa = result.scalars().first()
                if empresa:
                    # 1. Instruções do Agente
                    if empresa.ia_instrucoes_personalizadas:
                        context_xml += f"<instrucoes_agente>\n{empresa.ia_instrucoes_personalizadas}\n</instrucoes_agente>\n"
                    
                    # 2. Identidade e tom
                    if getattr(empresa, "ia_personalidade", None):
                        context_xml += f"<identidade_tom_ia>\n{empresa.ia_personalidade}\n</identidade_tom_ia>\n"
                    
                    # 3. Contexto Institucional
                    context_inst = f"Nome da Empresa: {empresa.nome_empresa}\n"
                    if empresa.area_atuacao:
                        context_inst += f"Área de Atuação: {empresa.area_atuacao}\n"
                    if getattr(empresa, 'informacoes_adicionais', None):
                        context_inst += f"Informações Adicionais: {empresa.informacoes_adicionais}\n"
                    context_xml += f"<contexto_institucional>\n{context_inst}</contexto_institucional>\n"

                    # 4. Saudação (Apenas se for a primeira mensagem)
                    if is_primeira_mensagem and empresa.mensagem_saudacao:
                        context_xml += f"<saudacao_obrigatoria>\n{empresa.mensagem_saudacao}\n</saudacao_obrigatoria>\n"

                    # 5. Instrução de coleta de nome (condicional)
                    coletar_nome = getattr(empresa, 'coletar_nome', True)
                    if coletar_nome is None:
                        coletar_nome = True
                    if coletar_nome:
                        context_xml += "<coleta_nome>\nSe ainda não souber o nome do cliente, pergunte de forma natural e amigável antes de prosseguir com o atendimento.\n</coleta_nome>\n"
                    else:
                        context_xml += "<coleta_nome>\nNão peça o nome do cliente. Prossiga a conversa normalmente mesmo sem saber o nome. Trate-o com cordialidade sem se referir ao nome.\n</coleta_nome>\n"

        except (ValueError, TypeError):
            pass

    prompt = f"""<role>
Você é um atendente inteligente e conciso. Seu objetivo é interagir com o lead de forma natural, usando o <contexto_institucional> apenas como consulta para embasar suas respostas.
</role>

{context_xml}

<regras_comportamento>
1. ESTRUTURA: Use as <instrucoes_agente> como sua lógica principal de atuação.
2. PERSONALIDADE: Siga rigorosamente o conteúdo em <identidade_tom_ia>.
3. CONCISÃO: Seja extremamente breve. Responda em no máximo duas ou três frases curtas.
4. DIRETRIZ DE FORMATAÇÃO (CRÍTICO): Você está se comunicando exclusivamente pelo WhatsApp. NUNCA use formatação Markdown padrão.
   - Proibido usar ** para negrito. Use apenas um asterisco: *texto*.
   - Proibido usar # ou ## para títulos.
   - Para itálico, use _texto_.
   - Mantenha os parágrafos curtos e não polua a tela com formatações excessivas.
5. DIRETRIZ DE OBJETIVIDADE: NUNCA peça permissão para enviar uma informação ou oferta (ex: Posso te mostrar?). Se você tem a informação (como preços, cursos ou bolsas), ENVIE IMEDIATAMENTE. Se faltar contexto para buscar, faça a pergunta de forma direta. Proibido usar excesso de confirmações como Perfeito, Que ótimo, seguidas.
6. SAUDAÇÃO: Se existir uma <saudacao_obrigatoria>, você DEVE usá-la como sua primeira interação. Se NÃO existir tal tag, significa que a conversa já está em andamento ou não há saudação definida; nesse caso, vá direto ao ponto sem dizer "Olá" ou se apresentar.
7. VARIAÇÃO: Varie seu vocabulário. Nunca repita a mesma saudação ou estrutura de frase usada nas suas mensagens anteriores.
</regras_comportamento>
"""
    return prompt


async def save_simulator_history(
    empresa_id: str,
    sessao_id: str,
    pergunta: str,
    resposta: str = None,
    conexao_id: str | None = None,
):
    """
    Função utilitária para fingir a gravação de conversa no simulador ou em Lead genérico para o app.
    No simulador, como não existe Lead real com o 'telefone' sessao_id, ele procura ou insere um Mock
    """
    import uuid
    from db.database import AsyncSessionLocal
    from db.models import CRMLead, MensagemHistorico, CRMFunil, CRMEtapa
    from sqlalchemy import select
    
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            # Tentar achar o lead da sessao, senão cria um pra não dar amnésia na View do Inbox.
            result = await session.execute(
                select(CRMLead).where(CRMLead.empresa_id == empresa_uuid, CRMLead.telefone_contato == sessao_id)
            )
            lead = result.scalars().first()
            
            if not lead:
                # Criar lead fake
                # Acha o funil e etapa basica
                result_funil = await session.execute(select(CRMFunil).where(CRMFunil.empresa_id == empresa_uuid))
                funil = result_funil.scalars().first()
                etapa_id_base = None
                if funil:
                    result_etap = await session.execute(select(CRMEtapa).where(CRMEtapa.funil_id == funil.id))
                    etapa = result_etap.scalars().first()
                    if etapa:
                        etapa_id_base = etapa.id
                        
                lead = CRMLead(
                    empresa_id=empresa_uuid,
                    nome_contato="[Simulador]",
                    telefone_contato=sessao_id,
                    etapa_id=etapa_id_base
                )
                session.add(lead)
                await session.flush()
                
            # Adiciona mensagem do usuário
            msg_usuario = MensagemHistorico(
                lead_id=lead.id,
                conexao_id=uuid.UUID(conexao_id) if conexao_id else None,
                texto=pergunta,
                from_me=False,
            )
            session.add(msg_usuario)
            
            # Adiciona mensagem da IA se fornecida
            if resposta:
                msg_ia = MensagemHistorico(
                    lead_id=lead.id,
                    conexao_id=uuid.UUID(conexao_id) if conexao_id else None,
                    texto=resposta,
                    from_me=True,
                )
                session.add(msg_ia)
            
            await session.commit()
            
    except Exception as e:
        print(f"Falha gravando simulação no DB: {e}")
