from __future__ import annotations

import pathlib
import unittest

from db.models import ChannelContact, ChannelConversation, Conexao, MensagemHistorico
from db.manual_migrations import _split_sql_statements
from app.schemas import ConexaoCreate, ConexaoResponse, MensagemHistoricoResponse


ROOT = pathlib.Path(__file__).resolve().parents[2]


class MultichannelPersistenceModelTests(unittest.TestCase):
    def test_sqlalchemy_mappers_configure_with_composite_tenant_fks(self) -> None:
        from sqlalchemy.orm import configure_mappers

        configure_mappers()

    def test_connection_has_channel_provider_and_official_account_fields(self) -> None:
        columns = Conexao.__table__.columns

        for name in (
            "channel",
            "provider",
            "is_default",
            "external_account_id",
            "waba_id",
            "phone_number_id",
            "instagram_business_account_id",
            "page_id",
            "display_number",
            "metadata_json",
            "atualizado_em",
        ):
            self.assertIn(name, columns)

        unique_names = {constraint.name for constraint in Conexao.__table__.constraints}
        self.assertIn("uq_conexoes_empresa_id_id", unique_names)

    def test_channel_contact_identity_is_connection_scoped(self) -> None:
        columns = ChannelContact.__table__.columns
        for name in (
            "empresa_id",
            "conexao_id",
            "lead_id",
            "channel",
            "provider",
            "external_contact_id",
            "normalized_address",
            "phone_e164",
            "username",
            "display_name",
            "profile_photo_url",
            "metadata_json",
            "first_seen_at",
            "last_seen_at",
        ):
            self.assertIn(name, columns)

        unique_names = {constraint.name for constraint in ChannelContact.__table__.constraints}
        self.assertIn("uq_channel_contacts_identity", unique_names)
        self.assertIn("uq_channel_contacts_empresa_id_id", unique_names)
        self.assertIn("fk_channel_contacts_empresa_conexao", unique_names)
        self.assertIn("fk_channel_contacts_empresa_lead", unique_names)

    def test_channel_conversation_identity_is_connection_scoped(self) -> None:
        columns = ChannelConversation.__table__.columns
        for name in (
            "empresa_id",
            "conexao_id",
            "contact_id",
            "lead_id",
            "channel",
            "provider",
            "conversation_key",
            "external_conversation_id",
            "external_thread_id",
            "last_message_at",
            "last_inbound_at",
            "last_outbound_at",
            "metadata_json",
        ):
            self.assertIn(name, columns)

        unique_names = {constraint.name for constraint in ChannelConversation.__table__.constraints}
        self.assertIn("uq_channel_conversations_identity", unique_names)
        self.assertIn("uq_channel_conversations_empresa_id_id", unique_names)
        self.assertIn("fk_channel_conversations_empresa_conexao", unique_names)
        self.assertIn("fk_channel_conversations_empresa_contact", unique_names)
        self.assertIn("fk_channel_conversations_empresa_lead", unique_names)

    def test_message_history_can_reference_channel_conversation(self) -> None:
        columns = MensagemHistorico.__table__.columns
        for name in (
            "empresa_id",
            "channel_conversation_id",
            "channel",
            "provider",
            "external_message_id",
            "external_conversation_id",
            "direction",
            "message_status",
            "provider_status",
            "metadata_json",
        ):
            self.assertIn(name, columns)

        constraint_names = {constraint.name for constraint in MensagemHistorico.__table__.constraints}
        self.assertIn("fk_mensagens_historico_empresa_lead", constraint_names)
        self.assertIn("fk_mensagens_historico_empresa_conexao", constraint_names)
        self.assertIn("fk_mensagens_historico_empresa_conversation", constraint_names)

    def test_metadata_json_rejects_sensitive_input(self) -> None:
        with self.assertRaises(ValueError):
            ConexaoCreate(
                tipo="meta",
                metadata_json={"access_token": "secret-value"},
            )

    def test_metadata_json_is_redacted_on_responses(self) -> None:
        conexao = ConexaoResponse(
            id="11111111-1111-4111-8111-111111111111",
            empresa_id="22222222-2222-4222-8222-222222222222",
            tipo="meta",
            nome_instancia="meta-wa",
            status="ativo",
            metadata_json={"token": "secret-value", "display": "ok"},
            webhook_url="https://example.invalid/webhook",
            webhook_path="/webhook",
        )
        self.assertEqual(conexao.metadata_json["token"], "***REDACTED***")
        self.assertEqual(conexao.metadata_json["display"], "ok")

        mensagem = MensagemHistoricoResponse(
            id="msg-1",
            texto="oi",
            metadata_json={"nested": {"api_key": "secret-value"}},
        )
        self.assertEqual(mensagem.metadata_json["nested"]["api_key"], "***REDACTED***")

    def test_manual_migration_is_not_wired_into_backend_startup(self) -> None:
        main_source = (ROOT / "app/api/main.py").read_text(encoding="utf-8")
        entrypoint = (ROOT / "docker/backend-entrypoint.sh").read_text(encoding="utf-8")

        self.assertNotIn("ensure_multichannel_persistence_tables", main_source)
        self.assertNotIn("phase2_multichannel_persistence", entrypoint)

    def test_upgrade_and_rollback_sql_exist(self) -> None:
        upgrade = ROOT / "db/migrations/2026_07_17_phase2_multichannel_persistence.sql"
        rollback = ROOT / "db/migrations/2026_07_17_phase2_multichannel_persistence_rollback.sql"

        self.assertTrue(upgrade.exists())
        self.assertTrue(rollback.exists())

        upgrade_sql = upgrade.read_text(encoding="utf-8")
        rollback_sql = rollback.read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS channel_contacts", upgrade_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS channel_conversations", upgrade_sql)
        self.assertIn("ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS channel", upgrade_sql)
        self.assertIn("uq_conexoes_empresa_id_id", upgrade_sql)
        self.assertIn("uq_crm_leads_empresa_id_id", upgrade_sql)
        self.assertIn("uq_channel_contacts_empresa_id_id", upgrade_sql)
        self.assertIn("uq_channel_conversations_empresa_id_id", upgrade_sql)
        self.assertIn("fk_channel_contacts_empresa_conexao", upgrade_sql)
        self.assertIn("fk_channel_conversations_empresa_contact", upgrade_sql)
        self.assertIn("fk_mensagens_historico_empresa_lead", upgrade_sql)
        self.assertIn("precheck failed", upgrade_sql)
        self.assertIn("postcheck failed", upgrade_sql)
        self.assertIn("DROP TABLE IF EXISTS channel_conversations", rollback_sql)
        self.assertIn("DROP TABLE IF EXISTS channel_contacts", rollback_sql)
        self.assertIn("Rollback destrutivo", rollback_sql)

    def test_manual_migration_sql_splitter_preserves_do_blocks(self) -> None:
        statements = _split_sql_statements(
            """
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            DO $$
            BEGIN
                IF true THEN
                    RAISE NOTICE 'ok; still same statement';
                END IF;
            END $$;
            SELECT 'done';
            """
        )

        self.assertEqual(3, len(statements))
        self.assertIn("RAISE NOTICE 'ok; still same statement'", statements[1])


if __name__ == "__main__":
    unittest.main()
