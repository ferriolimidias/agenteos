"""
Merge de leads duplicados por (empresa_id, telefone sanitizado).

Regra de negócio:
  - Agrupa por empresa_id + regexp_replace(telefone_contato, '\\D', '', 'g')
  - Mantém o lead mais antigo (MIN(criado_em), empate por MIN(id))
  - Move mensagens e logs do lead duplicado para o lead mantido
  - Remove duplicatas funcionais em follow-up logs / histórico de transferência
  - Deleta os leads duplicados

Uso:
  python migrate_merge_duplicate_crm_leads.py           # executa merge
  python migrate_merge_duplicate_crm_leads.py --dry-run # apenas relatório

Execute ANTES de rodar migrate_crm_leads_unique_telefone.py (unique index).
"""

from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db.database import engine


SQL_REPORT = """
WITH base AS (
    SELECT
        id,
        empresa_id,
        telefone_contato,
        regexp_replace(COALESCE(telefone_contato, ''), '\\D', '', 'g') AS tel_digits,
        criado_em
    FROM crm_leads
    WHERE telefone_contato IS NOT NULL
      AND length(regexp_replace(COALESCE(telefone_contato, ''), '\\D', '', 'g')) > 0
),
dup_groups AS (
    SELECT empresa_id, tel_digits, COUNT(*) AS n
    FROM base
    GROUP BY empresa_id, tel_digits
    HAVING COUNT(*) > 1
)
SELECT
    (SELECT COUNT(*) FROM dup_groups) AS grupos_duplicados,
    (SELECT COALESCE(SUM(n - 1), 0) FROM dup_groups) AS leads_extras_a_remover;
"""


SQL_MERGE = r"""
-- 1) Mapa: cada lead -> canonical (mais antigo) dentro do grupo (empresa, telefone_digits)
WITH base AS (
    SELECT
        id,
        empresa_id,
        telefone_contato,
        regexp_replace(COALESCE(telefone_contato, ''), '\\D', '', 'g') AS tel_digits,
        criado_em
    FROM crm_leads
    WHERE telefone_contato IS NOT NULL
      AND length(regexp_replace(COALESCE(telefone_contato, ''), '\\D', '', 'g')) > 0
),
ranked AS (
    SELECT
        id,
        empresa_id,
        tel_digits,
        ROW_NUMBER() OVER (
            PARTITION BY empresa_id, tel_digits
            ORDER BY criado_em ASC NULLS LAST, id ASC
        ) AS rn
    FROM base
),
lead_map AS (
    SELECT
        r.id AS lead_id,
        FIRST_VALUE(r.id) OVER (
            PARTITION BY r.empresa_id, r.tel_digits
            ORDER BY r.rn ASC
        ) AS canonical_id
    FROM ranked r
),
dup_only AS (
    SELECT lead_id, canonical_id
    FROM lead_map
    WHERE lead_id <> canonical_id
),

-- 2) Mensagens
upd_msgs AS (
    UPDATE mensagens_historico mh
    SET lead_id = d.canonical_id
    FROM dup_only d
    WHERE mh.lead_id = d.lead_id
    RETURNING mh.id
),

-- 3) Follow-up logs: mover e depois deduplicar por (lead, config_followup)
upd_fu AS (
    UPDATE lead_followup_logs l
    SET lead_id = d.canonical_id
    FROM dup_only d
    WHERE l.lead_id = d.lead_id
    RETURNING l.id
),
del_fu_dup AS (
    DELETE FROM lead_followup_logs l
    WHERE id IN (
        SELECT id FROM (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY lead_id, config_followup_id
                    ORDER BY data_envio DESC NULLS LAST, criado_em DESC NULLS LAST, id DESC
                ) AS rn
            FROM lead_followup_logs
        ) x
        WHERE rn > 1
    )
    RETURNING id
),

-- 4) Histórico de transferência: mover + deduplicar por instante exato (fallback seguro)
upd_ht AS (
    UPDATE historico_transferencia h
    SET lead_id = d.canonical_id
    FROM dup_only d
    WHERE h.lead_id = d.lead_id
    RETURNING h.id
),
del_ht_dup AS (
    DELETE FROM historico_transferencia h
    WHERE id IN (
        SELECT id FROM (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY lead_id, criado_em, COALESCE(destino_id::text, ''), COALESCE(motivo_ia, ''), COALESCE(resumo_enviado, '')
                    ORDER BY id DESC
                ) AS rn
            FROM historico_transferencia
        ) x
        WHERE rn > 1
    )
    RETURNING id
),

-- 5) Agendamentos (opcional SET NULL -> consolidar no canonical)
upd_ag AS (
    UPDATE agendamentos_locais a
    SET lead_id = d.canonical_id
    FROM dup_only d
    WHERE a.lead_id = d.lead_id
    RETURNING a.id
),

-- 6) Normalizar telefone no canonical para formato dígitos-only
upd_canon_phone AS (
    UPDATE crm_leads cl
    SET telefone_contato = b.tel_digits
    FROM (
        SELECT DISTINCT ON (canonical_id)
            m.canonical_id,
            x.tel_digits
        FROM dup_only m
        JOIN base x ON x.id = m.canonical_id
        ORDER BY canonical_id
    ) b
    WHERE cl.id = b.canonical_id
      AND (cl.telefone_contato IS DISTINCT FROM b.tel_digits)
    RETURNING cl.id
),

-- 7) Enriquecer dados do canonical a partir dos duplicados (tags JSONB + campos simples)
enrich AS (
    UPDATE crm_leads keeper
    SET
        nome_contato = CASE
            WHEN keeper.nome_contato IS NULL OR trim(keeper.nome_contato) = ''
                OR lower(trim(keeper.nome_contato)) IN ('usuário (auto)', 'usuario (auto)')
                OR keeper.nome_contato ~ '^[\d\+\-\(\)\s]+$'
            THEN COALESCE(NULLIF(trim(src.nome_contato), ''), keeper.nome_contato)
            ELSE keeper.nome_contato
        END,
        historico_resumo = CASE
            WHEN keeper.historico_resumo IS NULL OR trim(keeper.historico_resumo) = ''
            THEN src.historico_resumo
            WHEN src.historico_resumo IS NOT NULL AND length(trim(src.historico_resumo)) > length(trim(COALESCE(keeper.historico_resumo, '')))
            THEN src.historico_resumo
            ELSE keeper.historico_resumo
        END,
        foto_url = COALESCE(keeper.foto_url, src.foto_url),
        foto_atualizada_em = CASE
            WHEN keeper.foto_url IS NULL AND src.foto_url IS NOT NULL THEN src.foto_atualizada_em
            ELSE keeper.foto_atualizada_em
        END,
        gclid = COALESCE(keeper.gclid, src.gclid),
        fbclid = COALESCE(keeper.fbclid, src.fbclid),
        valor_conversao = COALESCE(keeper.valor_conversao, src.valor_conversao),
        bot_pausado_ate = CASE
            WHEN keeper.bot_pausado_ate IS NULL THEN src.bot_pausado_ate
            WHEN src.bot_pausado_ate IS NULL THEN keeper.bot_pausado_ate
            ELSE GREATEST(keeper.bot_pausado_ate, src.bot_pausado_ate)
        END,
        status_atendimento = CASE
            WHEN lower(COALESCE(src.status_atendimento, '')) = 'concluido' THEN 'concluido'
            ELSE keeper.status_atendimento
        END,
        ia_ativa = keeper.ia_ativa AND src.ia_ativa,
        tags = (
            SELECT COALESCE(jsonb_agg(DISTINCT elem), '[]'::jsonb)
            FROM jsonb_array_elements(
                COALESCE(keeper.tags, '[]'::jsonb) || COALESCE(src.tags, '[]'::jsonb)
            ) AS t(elem)
        ),
        dados_adicionais = keeper.dados_adicionais || COALESCE(src.dados_adicionais, '{}'::jsonb)
    FROM crm_leads src
    JOIN dup_only d ON src.id = d.lead_id
    WHERE keeper.id = d.canonical_id
    RETURNING keeper.id
),

-- 8) Apagar duplicados
del_leads AS (
    DELETE FROM crm_leads cl
    USING dup_only d
    WHERE cl.id = d.lead_id
    RETURNING cl.id
)

SELECT
    (SELECT COUNT(*) FROM dup_only) AS pares_mapeados,
    (SELECT COUNT(*) FROM upd_msgs) AS mensagens_movidas,
    (SELECT COUNT(*) FROM upd_fu) AS followup_logs_movidos,
    (SELECT COUNT(*) FROM del_fu_dup) AS followup_logs_duplicados_removidos,
    (SELECT COUNT(*) FROM upd_ht) AS transferencias_movidas,
    (SELECT COUNT(*) FROM del_ht_dup) AS transferencias_duplicadas_removidas,
    (SELECT COUNT(*) FROM upd_ag) AS agendamentos_relinkados,
    (SELECT COUNT(*) FROM upd_canon_phone) AS canonical_telefone_normalizado,
    (SELECT COUNT(*) FROM enrich) AS canonical_enriquecidos,
    (SELECT COUNT(*) FROM del_leads) AS leads_duplicados_removidos;
"""


async def main() -> None:
    parser = argparse.ArgumentParser(description="Merge de CRM leads duplicados por telefone sanitizado.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Somente exibe quantidade de grupos duplicados; não altera o banco.",
    )
    args = parser.parse_args()

    async with engine.connect() as conn:
        rep = await conn.execute(text(SQL_REPORT))
        row = rep.mappings().first()
        grupos = int(row["grupos_duplicados"] or 0)
        extras = int(row["leads_extras_a_remover"] or 0)
        print(f"[RELATÓRIO] Grupos duplicados: {grupos} | Leads extras a remover: {extras}")

        if args.dry_run:
            print("[DRY-RUN] Nenhuma alteração aplicada.")
            return

        if grupos == 0:
            print("[OK] Nenhuma duplicata encontrada. Nada a fazer.")
            return

    async with engine.begin() as conn:
        stats = await conn.execute(text(SQL_MERGE))
        s = stats.mappings().first()
        print(
            "[MERGE CONCLUÍDO]",
            {k: int(v or 0) for k, v in dict(s).items()},
        )


if __name__ == "__main__":
    asyncio.run(main())
