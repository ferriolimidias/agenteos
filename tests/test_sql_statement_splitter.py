"""Regression contracts for the PostgreSQL migration runner's lexical splitter."""
from pathlib import Path

import pytest

from db.manual_migrations import _split_sql_statements


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("", []),
        (" ; ; \n", []),
        ("-- comment; no command", []),
        ("/* outer; /* nested; */ done */", []),
        ("SELECT 1;SELECT 2", ["SELECT 1", "SELECT 2"]),
        ("SELECT 1;; -- trailing; comment", ["SELECT 1"]),
        ("-- before;\nSELECT 1; -- after;", ["-- before;\nSELECT 1"]),
        ("SELECT 1 -- middle;\n;SELECT 2", ["SELECT 1 -- middle;", "SELECT 2"]),
        ("SELECT/* inline; */1;", ["SELECT/* inline; */1"]),
        ("/* a; /* b; */ c; */SELECT 1;", ["/* a; /* b; */ c; */SELECT 1"]),
        ("SELECT 'a;b';SELECT 'it''s;c';", ["SELECT 'a;b'", "SELECT 'it''s;c'"]),
        ('SELECT "a;""b";SELECT 2', ['SELECT "a;""b"', "SELECT 2"]),
        (r"SELECT E'it\'s;a';SELECT 2;", [r"SELECT E'it\'s;a'", "SELECT 2"]),
        (r"SELECT E'\\';SELECT 2;", [r"SELECT E'\\'", "SELECT 2"]),
        (r"SELECT '\';SELECT 2;", [r"SELECT '\'", "SELECT 2"]),
        ("SELECT '-- /* ; */';SELECT 2", ["SELECT '-- /* ; */'", "SELECT 2"]),
        ("SELECT $$a; ' -- /*$$;SELECT 2", ["SELECT $$a; ' -- /*$$", "SELECT 2"]),
        ("SELECT $tag_1$a; $$; ' $tag_1$;SELECT 2", ["SELECT $tag_1$a; $$; ' $tag_1$", "SELECT 2"]),
        ("SELECT $1;SELECT 2", ["SELECT $1", "SELECT 2"]),
        ("SELECT foo$tag$;SELECT 2", ["SELECT foo$tag$", "SELECT 2"]),
        ("SELECT $é$a;b$é$;SELECT 2", ["SELECT $é$a;b$é$", "SELECT 2"]),
        ("SELECT 1 -- eof;", ["SELECT 1 -- eof;"]),
        ("-- crlf;\r\nSELECT 1;", ["-- crlf;\r\nSELECT 1"]),
        ("SELECT 1/* eof; */", ["SELECT 1/* eof; */"]),
    ],
)
def test_separators_follow_postgresql_lexical_context(sql, expected):
    assert _split_sql_statements(sql) == expected


@pytest.mark.parametrize("sql", ["SELECT 'unfinished", 'SELECT "unfinished', "SELECT $$unfinished", "SELECT $tag$unfinished", "/* unfinished", "/* outer /* inner */"])
def test_unterminated_constructs_fail_before_any_statement_is_executed(sql):
    with pytest.raises(ValueError, match="Unterminated"):
        _split_sql_statements("SELECT 1;" + sql)


def test_exact_commercial_mapping_regression_preserves_comments_and_update():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "db/migrations/2026_08_12_commercial_mapping_contract.sql").read_text()
    statements = _split_sql_statements(sql)
    assert len(statements) == 1
    assert statements[0] == sql.strip().removesuffix(";")
    assert "-- No tenant identifiers are embedded; rows are changed" in statements[0]
    assert "UPDATE business_messaging_conversion_mappings AS mapping" in statements[0]


def test_real_do_and_function_bodies_are_not_fragmented():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "db/migrations/2026_10_09_004_legacy_conversion_delivery_evidence.sql").read_text()
    statements = _split_sql_statements(sql)
    functions = [s for s in statements if "CREATE OR REPLACE FUNCTION" in s]
    assert len(functions) == 1
    assert "RETURNS trigger LANGUAGE plpgsql AS $$" in functions[0]
    assert "RETURN NEW;" in functions[0]
    assert functions[0].endswith("$$")


def test_all_repository_migrations_have_complete_lexical_constructs():
    root = Path(__file__).resolve().parents[1]
    migrations = sorted((root / "db/migrations").glob("*.sql"))
    assert len(migrations) >= 74
    for migration in migrations:
        statements = _split_sql_statements(migration.read_text())
        if migration.name == "2026_07_24_instagram_credential_source_rollback.sql":
            # This documented manual rollback intentionally contains comments only.
            assert statements == []
        else:
            assert statements, migration.name
