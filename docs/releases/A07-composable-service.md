# A07 — contrato congelado

Autorização: solicitação integral A07 e Issue8, incluindo implementação, simulação/preview, publicação master, deploy independente emLEGACY, revisão, rollback e relatório. Não pedir novamente autorização para ações já explicitamente autorizadas. Baseline0f48b10ce0422489cd0d8bb447305442e7b6cbf0; backendA06/frontCRM23 confirmados. Não autorizado: atendimento modular real a clientes, egress de conversa real JEV, tags/envios/conversões/MetaLIVE reais. Contrato concretiza escopo aprovado; somente integrador registra ajustes diagnósticos antes do trabalho dependente, sem expandir critérios no fechamento.

## Objetivo e invariantes

Compor múltiplos assuntos na mesma resposta comercial, com nove módulos versionados, etapa canônica independente, preview imediatamente útil e fallback seguro. Basepersona/regras/prompt_builder/primeiro-turnoJSON/saudação, planner/tools/fiscal/ResponseGuard/FunnelSpec e dispatch preservados. Não alterar regras/preços/garantias/catálogo/tags/campanhas/follow-ups/pagamentos/transportes/analytics. Não gerar respostas por módulo, aplicar sugestões JEV, ou duplicar sender.

Hook opt-in em AtendimentoAgent depois das tools e antes da geraçãofinal. LEGACY mantém prompt/kwargs/resposta exatos e não chama classificação/JEV/stagequery/ledger novos. Primeiro turno permaneceLEGACY com motivo explícito quandoModularsolicitado. Uma resposta final, usando mesmo gerador e validação existentes; repairslegados continuam possíveis, sem alegar uma única chamada emtodo pipeline.

Registry9fixos: qualification, products_services, pricing_conditions, objections_questions, trust_guarantees, availability, scheduling, offer_closing, human_handoff. IDs/versões/instruções fixos; no texto cliente/JEV vira instrução sistema livre. Seleção multilateral determinística, máximo6módulos, prioridademáxima segurança/handoff, resolverconflitossemrepetição. Primary stage somente CRMLead→CRMEtapa→CRMFunil.empresa_id scoped ouunknown; stageinferidoJEV jamais sobrescreveCRM. Simuladorindicastagesintético. Fatos apenas fontes canônicas/config e toolsbem-sucedidas; ausente/obsoleto/conflitante exige abstenção/pergunta/fallback, nunca invenção.

Flagenv porUUIDtenant requestedLEGACY|MODULAR, defaultLEGACY; autorização real independente por tenant+canais explicitamente OFF. EfetivoMODULAR exigeambos+canal. Preview não altera flags; UIapenasmostraestado. SemDBmigration/configwrite; envprodução preservado, nenhumaativação nestaissue.

JEVreusaA06 config/gateway/typedclassification/ledgerbudget sem alterá-los. Semkey classificação determinística identificada. Previewegress opcional somente example_id estático server-ownedA06, sem override de mensagem, comconfigenable/key/budget e solicitação explícita. Texto custom nunca pode ser marcado synthetic paraegress. Reuso de decisão real exigeorigemconfiável,tenant/lead/canal/contexto atuais correspondentes,config/privacidadeA06 e autorização modularreal; mismatchedfallback. Providerfalha/timeout/budget/decisãobaixa confiança voltaLEGACY/local seguro comprovenance; semretry externoambíguo. Nenhuma tag aplicada.

LedgerA07 Redis separadoA06 por tenant, TTL7dias/teto500/payloadlimitadoallowlist: runrefaleatório,versãocomposição+modules,channel,stageid/status,origem/status/motivos e métricas. Nunca mensagem/prompt/resposta/fatos/config/secret/PII. Outputmetadata não equivale à persistência. Redisfalha explícita; MODULARprodução falha detrace pregeração→LEGACY, previewpode mostrartrace não persistido. Budgetinputestimado máximo4096tokens e deadline8s opt-in; nunca truncarbasecanônica, overbudget→promptLEGACYexato. Cancelamento/provider timeout não duplica dispatch nem retryambíguo.

Métricas: latência local realmente medida e separada de provider. Uso/custo reais somente receiptobservado comorigemreal/syntheticdistinta; no rendererlocal input/output/costprovider null. Estimativascaracteres explicitamente rotuladas; comparação mesmaentrada/condições semeconomia/precisão comprovada. InstrumentaçãoLLM opcional backwardcompatible e opt-in, preservandostr/kwargsLEGACY.

## API congelada

Prefixo tenant autenticado `/api/empresas/{empresa_id}/atendimento-modular`; requiretenantaccess e reviewerrestrito, 401/403/404/422, sembodytenantoverride.
- GET/status: schema_version/composition_version, mode/requested_mode/live_authorized/authorized_channels, modules[id/version/title], provider[jev_configured/authorized_real/status], ledger[available/retention_days/max_runs], simulation engine/local/noegress, limits.
- GET/examples: {items:[id,title,message,context_mode,facts_source]}; somente exemplos estáticosPT-BR.
- POST/preview: {example_id?:str,message?:str,context_mode:complete|incomplete|conflicting(defaultincomplete),channel:whatsapp|instagram|simulator,use_jev:false default}. XORexample/message; message≤2000chars; semlead_id/regras/preço/prompt/flags. use_jevtrue somenteexampleaprovadosemcustomoverride.
- Previewpuro: semgrafo/planner/tool/executors/LLMreal, criaçãolead/histórico/tags ou envio. Rendererlocalexplicitamentesimulado comfatos declaradossintéticosdoexemplo/contexto, umarespostaPT-BRcoerente eResponseGuard. Jamais alegar preços/garantias reais no cenário; contexto incompletoabstém.
- Resposta: schema_version/run_id/source/engine/mode/live_mode/composition_version,selected_modules[id/version/title/reason_codes],primary_stage[value/status/source],active_topics,response_text/legacy_response_text,fallback_reason,conflicts/missing_facts,guard[allowed/reason_codes],jev[status/source/run_id/provider_used/classification_version],metrics{legacy/modular:[input_tokens/output_tokens/cost_usd nullable,estimated_input_tokens,latency_ms,measurement_source],comparison:[actualdeltasnullquandoausentes,estimateddeltaexplicit]},trace[persisted],warnings.
- GET/runs eGET/runs/{UUID}: somente metadadosallowlist tenant, semconteúdo.

Frontend: ModularAttendancePreview SectionCard emTenantAgentes existente, statusLEGACY/catálogo+mensagemsintética/contextochannel/executar/comparelegacy/busy+erro+foco+retry, módulos/versões/stage/provenance/métricasunknown/trace. Semnewroute/root/menu/redesign, reusartokensUIFerrioliexistentes. Nenhumcontroleativa provider/live pelo preview.

## Ownership e allowlist

Backendworker: novos app/services/a07_*.py, app/api/routers/atendimento_modular.py, tests/services/test_a07*.py/tests/security/test_a07*.py efixturesA07; apenas hooknecessário app/agents/atendimento/agent.py e registroapp/api/main.py. Seusageinstrumentationnecessária app/agents/llm/openai_provider.py/base.py, opt-in backwardcompatible. Stagehelper opt-in evita alteraçãoapp/api/utils.py. NãoeditarA06/ResponseGuard/FunnelSpec/tools/tagservices/transports/DBmodels/migrations/deps.
Frontendworker: novo frontend/src/components/ModularAttendancePreview.jsx, frontend/src/utils/modularAttendance.js+test, inserçãomínimaTenantAgentes.jsx, CSSnovosóescopadopreviewsepreciso, frontend/scripts/smoke-a07-modular.cjs+fixtures. NãoeditarCRM23/api.js/nav/root/deps/outraspages.
Rootintegrador: docs/releases/A07-composable-service.md, atualizaçãoestreitaREADMEroadmap/A07estado; releaseartefacts/gates/backup/report/merge/publicação/deploy. Dois escritores emworktreesisolados, semreverteroutros. Auditor técnico e segurança somenteleitura; implementadornãoaprova si. Modelherança disponível, nãofixedmodelunsupported.

## Aceite congelado

1 Inventário promptpipeline/hooks/dependências/risco/rollback e9módulosversionados.
2 ≥3intenções simultâneas→multimódulos,stageindependente,umarespostacoerentePT-BR no previeweagentgerativomock.
3 Completo/incompleto/conflitante,humano/injection/versões/móduloinválido/budget/timeout/providererror/fallbackLEGACY semdouble-send.
4 Isolamentotenant/canal/contexto,401/403/404/422/reviewer403; flagsproduçãoLEGACYdefault+authorizationOFF. Semativação externa real.
5 Trilhapersistidametadata tenant/TTL/teto; Redisfail/status explícito, semPII.
6 JEVsemkey/localfunciona; integração configurada/autorizada mock+server-ownedexamples; erro/timeout/budget/dedupA06; nãoalegarproviderrealvalidado.
7 Comparação localversionada:latênciaobservada/estimativasrotuladas/uso-custorealunknown; syntheticreceipt não é providerreal.
8 Frontendbaseline178+novosNode22,lintfocado/build/artifactsingle-root; browserdesktop1440/notebook1280/mobile390 antes/depoispreview/busy/foco/retry/isolamento/nav/CRM23; todasAPIsefeitosinterceptadas.
9 BackendfocadosA07/agents/ResponseGuard/WhatsApp/A06/auth; suíteintegralbaseline+final porIDs comparada aos18históricosA06(1389pass, nãoverde). AmbientePG/Redisdescartável/mock/egressexternobloqueado; fixtureescriturávelsemadulterartestes.14protectedworkspace+imagemcandidata+pré+pósPASS; qualquerFAILbloqueiadeploy.
10 Allowlist/secret/deps/runtime/env/mounts/infra +duasrevisõesP0/P1zero; backendoverlayA06 efrontendpanel_releaseCRM23preservandonginxAnamnese, SHA/imagens exatos. Candidata envstartupskip/workersOFF/DBRedisisolados/providerOFF.
11 Publicarmastereimplantar somenteapps apósgates; semmigration ourecriarPG/Redis/Evolution. Preservarconfig/env/mounts/aliasbackend/workermode/flags, infraIDs/start/env. RollbackappsA06+CRM23preservado/dryrun, rollbackhealthautomático; validar saúdepública/UI/previewAPIcomdadossintéticossemnegóciomutação/14protected. Nenhumcanaryrealautorizado: STATUSDEPLOYED_NOT_REAL_VALIDATED.
12 Issue8update/close pelaunidadecomposição+previewoperacional comlimitesreaisexplícitos; relatórioúnico/pacotechecksum reports/a07-composable-service-20261013 todoscamposusuário+safeproductiontemplate; sóintegradorfecha. BackendCRM23problemapotencialforadoescopo.
