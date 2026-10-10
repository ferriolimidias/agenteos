# Contrato Hermes → AgentOS: `leoft.catalog.import`

Endpoint interno existente da API AgentOS:

```text
POST /api/internal/hermes/leoft/catalog/import?dry_run=true
POST /api/internal/hermes/leoft/catalog/import
```

O Hermes lê `/srv/leoft-media/current/catalog/catalogo_agentos_importacao_completa.json`, calcula o SHA-256 do arquivo e envia um único JSON autenticado. O AgentOS não acessa filesystem, SSH ou banco do Hermes.

Headers obrigatórios:

```text
X-Hermes-Event: leoft.catalog.import
X-Hermes-Timestamp: Unix timestamp
X-Hermes-Signature: HMAC-SHA256(corpo JSON bruto, HERMES_LEOFT_HMAC_SECRET)
X-Hermes-Idempotency-Key: chave estável da importação
Content-Type: application/json
```

O limite seguro atual é 16 MiB por requisição. Não há chunking nesta etapa: a decisão é payload integral, condicionada à confirmação futura de que o JSON real cabe no limite. O endpoint rejeita payload acima do limite antes da validação.

Contrato mínimo:

```json
{
  "event_type": "leoft.catalog.import",
  "schema_version": "1.0",
  "catalog_version": "...",
  "source_sha256": "64 hex chars",
  "generated_at": "2026-07-31T00:00:00Z",
  "source": "hermes",
  "expected_counts": {
    "entries": 1074,
    "media": 1074,
    "aliases": 2542,
    "signatures": 1074,
    "states": {
      "approved_by_ai": 20,
      "approved_manually": 0,
      "pending_review": 412,
      "unidentified": 642,
      "rejected": 0,
      "duplicate": 0
    }
  },
  "entries": [
    {
      "catalog_id": "...",
      "canonical_name": "...",
      "approval_status": "approved_by_ai|approved_manually|pending_review|unidentified",
      "aliases": ["..."],
      "media": [{"media_id": "...", "storage_path": "relative/path", "sha256": "64 hex chars"}],
      "signatures": [{"signature_key": "...", "signature_version": "v1", "signature_payload": {}}]
    }
  ]
}
```

O cliente Hermes deve executar primeiro `dry_run=true`, conferir `counts`, `catalog_version` e `source_sha256`, e somente depois repetir a mesma requisição sem `dry_run`. O arquivo original e a VPS não são alterados; apenas buffers temporários do cliente podem ser removidos após confirmação.
