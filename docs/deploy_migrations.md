# Deploy e migrations

Antes de ativar uma nova imagem do backend, execute as migrations SQL na ordem aprovada para o ambiente.

Ordem mínima para a base multicanal atual:

1. `db/migrations/2026_07_17_phase2_multichannel_persistence.sql`
2. `db/migrations/2026_07_20_phase_a_operational_foundation.sql`
3. `db/migrations/2026_07_21_phase_b_meta_templates.sql`

A Fase C não deve ser aplicada sem gate humano explícito.

O backend executa `scripts/check_schema_compatibility.py` no startup para verificar colunas/constraints críticas antes de operar. O check não aplica migration automaticamente; schema incompatível faz o backend falhar de forma clara.

