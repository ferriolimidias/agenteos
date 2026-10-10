# Catálogo LeoFT no AgentOS

O PostgreSQL `agent_os` é a única fonte operacional do catálogo LeoFT. O catálogo, aliases, metadados de mídia, assinaturas, versões, importações, aprovações, rejeições e auditoria vivem nas tabelas `leoft_catalog_*`, `leoft_media_*` e `leoft_approval_events`.

Os arquivos de mídia ficam em storage/filesystem; o PostgreSQL guarda somente metadados, caminhos, hashes e estados. O novo Training Engine não usa SQLite, FTS5, arquivos JSON como banco, cache paralelo ou embeddings. `pgvector` fica reservado para RAG futuro.

O catálogo e as mídias são globais à biblioteca LeoFT. Planos e acessos continuam ligados a `empresa_id` nas tabelas LeoFT existentes. As aprovações são globais nesta etapa: AG1–AG5 não definem uma aprovação por empresa; os campos opcionais `source_plan_id` e `source_exercise_id` preservam a origem quando houver uma.

Hermes deverá acessar o AgentOS por contrato interno autenticado, nunca diretamente pelo banco.

## Importação

```text
/tmp/agenteos-venv/bin/python scripts/import_leoft_catalog.py /caminho/catalogo_agentos_importacao_completa.json --dry-run
/tmp/agenteos-venv/bin/python scripts/import_leoft_catalog.py /caminho/catalogo_agentos_importacao_completa.json --apply
```

O documento precisa conter `version` e `entries`. Cada entrada contém `catalog_id`, `canonical_name`, `approval_status` e, opcionalmente, `aliases`, `media` e `signatures`. Cada mídia exige `media_id`, `storage_path` e SHA-256 hexadecimal; cada assinatura exige `signature_key`, `signature_version` e `signature_payload`. A importação valida o hash do arquivo quando `source_sha256` está declarado, usa UPSERT apenas pelos IDs oficiais e é transacional/idempotente.
