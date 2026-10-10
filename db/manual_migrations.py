from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.startup_database_mode import StartupDatabaseMode


MIGRATIONS_EMPRESAS_PROMPTS = (
    "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS atendente_prompt TEXT;",
    "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS conversion_monitor_prompt TEXT;",
)



MULTICHANNEL_PERSISTENCE_SQL_PATH = (
    Path(__file__).resolve().parent / "migrations" / "2026_07_17_phase2_multichannel_persistence.sql"
)
TRAFFIC_META_MVP_SQL_PATH = (
    Path(__file__).resolve().parent / "migrations" / "2026_08_11_traffic_meta_mvp_foundation.sql"
)


def _split_sql_statements(sql: str) -> list[str]:
    """Split PostgreSQL commands without treating quoted/commented ';' as SQL.

    Keep the SQL text intact, including comments. Standard strings use doubled
    quotes; PostgreSQL E strings additionally allow backslash escapes. Block
    comments nest, and dollar tags must follow PostgreSQL identifier rules.
    Comment-only input is not an executable statement. Reject unfinished lexical
    constructs before a caller can execute part of a malformed migration.
    """
    statements: list[str] = []
    current: list[str] = []
    single_quote = False
    double_quote = False
    dollar_tag: str | None = None
    line_comment = False
    block_depth = 0
    escape_string = False
    has_code = False
    index = 0

    while index < len(sql):
        char = sql[index]

        if line_comment:
            current.append(char)
            index += 1
            if char in "\r\n":
                line_comment = False
            continue

        if block_depth:
            pair = sql[index : index + 2]
            if pair in {"/*", "*/"}:
                block_depth += 1 if pair == "/*" else -1
                current.append(pair)
                index += 2
            else:
                current.append(char)
                index += 1
            continue

        if dollar_tag:
            if sql.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
                continue
            current.append(char)
            index += 1
            continue

        if single_quote:
            current.append(char)
            if escape_string and char == "\\" and index + 1 < len(sql):
                current.append(sql[index + 1])
                index += 2
                continue
            if char == "'" and sql[index + 1 : index + 2] == "'":
                current.append("'")
                index += 2
                continue
            if char == "'":
                single_quote = False
            index += 1
            continue

        if double_quote:
            current.append(char)
            if char == '"' and sql[index + 1 : index + 2] == '"':
                current.append('"')
                index += 2
                continue
            if char == '"':
                double_quote = False
            index += 1
            continue

        pair = sql[index : index + 2]
        if pair == "--":
            line_comment = True
            current.append(pair)
            index += 2
            continue

        if pair == "/*":
            block_depth = 1
            current.append(pair)
            index += 2
            continue

        if char == "'":
            single_quote = True
            escape_string = (
                index > 0
                and sql[index - 1] in "eE"
                and (index < 2 or not (sql[index - 2].isalnum() or sql[index - 2] in "_$"))
            )
            has_code = True
            current.append(char)
            index += 1
            continue

        if char == '"':
            double_quote = True
            has_code = True
            current.append(char)
            index += 1
            continue

        if char == "$" and (index == 0 or not (sql[index - 1].isalnum() or sql[index - 1] in "_$")):
            end = sql.find("$", index + 1)
            if end != -1:
                candidate = sql[index : end + 1]
                tag = candidate[1:-1]
                if candidate == "$$" or (
                    tag
                    and (tag[0].isalpha() or tag[0] == "_")
                    and all(c.isalnum() or c == "_" for c in tag)
                ):
                    dollar_tag = candidate
                    has_code = True
                    current.append(candidate)
                    index = end + 1
                    continue

        if char == ";":
            statement = "".join(current).strip()
            if has_code and statement:
                statements.append(statement)
            current = []
            has_code = False
            index += 1
            continue

        current.append(char)
        if not char.isspace():
            has_code = True
        index += 1

    if single_quote or double_quote or dollar_tag or block_depth:
        raise ValueError("Unterminated SQL string, quoted identifier, dollar quote or block comment")
    statement = "".join(current).strip()
    if has_code and statement:
        statements.append(statement)
    return statements


async def ensure_empresas_prompt_columns(engine: AsyncEngine) -> None:
    """
    Garante compatibilidade da tabela empresas para campos novos do modelo.
    É idempotente e pode ser executada em todo startup.
    """
    StartupDatabaseMode.resolve().require_database_actions(
        "ensure_empresas_prompt_columns"
    )
    async with engine.begin() as conn:
        for statement in MIGRATIONS_EMPRESAS_PROMPTS:
            await conn.execute(text(statement))



async def ensure_multichannel_persistence_tables(engine: AsyncEngine) -> None:
    """
    Cria a persistencia multicanal em ambiente controlado.

    Esta funcao nao e chamada pelo startup do backend para evitar aplicar
    migration em producao sem gate humano.
    """
    StartupDatabaseMode.resolve().require_database_actions(
        "ensure_multichannel_persistence_tables"
    )
    async with engine.begin() as conn:
        sql = MULTICHANNEL_PERSISTENCE_SQL_PATH.read_text(encoding="utf-8")
        for statement in _split_sql_statements(sql):
            await conn.execute(text(statement))
async def ensure_traffic_meta_mvp_tables(engine: AsyncEngine) -> None:
    """Cria a fundacao Traffic Meta MVP em ambiente controlado."""
    StartupDatabaseMode.resolve().require_database_actions("ensure_traffic_meta_mvp_tables")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))
        sql = TRAFFIC_META_MVP_SQL_PATH.read_text(encoding="utf-8")
        for statement in _split_sql_statements(sql):
            await conn.execute(text(statement))
