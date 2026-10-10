from __future__ import annotations

import asyncio
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.startup_database_mode import StartupDatabaseMode, StartupDatabaseModeError


ROOT = Path(__file__).resolve().parents[1]
BACKEND_RUNTIME_AVAILABLE = all(
    importlib.util.find_spec(package) is not None
    for package in ("fastapi", "dotenv", "sqlalchemy")
)


class StartupDatabaseModeTests(unittest.TestCase):
    def test_absent_mode_preserves_normal_startup(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(StartupDatabaseMode.resolve(), StartupDatabaseMode.INIT)

    def test_skip_requires_explicit_accepted_value(self):
        self.assertEqual(StartupDatabaseMode.resolve("skip"), StartupDatabaseMode.SKIP)
        self.assertEqual(StartupDatabaseMode.resolve(" INIT "), StartupDatabaseMode.INIT)

    def test_invalid_value_fails_closed_before_database_work(self):
        with self.assertRaisesRegex(StartupDatabaseModeError, "Expected one of: init, migrate, skip"):
            StartupDatabaseMode.resolve("anything-else")

    @unittest.skipUnless(BACKEND_RUNTIME_AVAILABLE, "backend dependencies are required")
    def test_skip_blocks_direct_init_and_migration_scripts(self):
        import init_db
        import run_migrations

        with patch.dict(os.environ, {"STARTUP_DATABASE_MODE": "skip"}, clear=False):
            with self.assertRaises(StartupDatabaseModeError):
                asyncio.run(init_db.init_models())
            with self.assertRaises(StartupDatabaseModeError):
                asyncio.run(run_migrations.run_migrations())

    @unittest.skipUnless(BACKEND_RUNTIME_AVAILABLE, "backend dependencies are required")
    def test_skip_blocks_all_manual_schema_mutators(self):
        from db import manual_migrations

        with patch.dict(os.environ, {"STARTUP_DATABASE_MODE": "skip"}, clear=False):
            mutator_names = (
                "ensure_empresas_prompt_columns",
                "ensure_multichannel_persistence_tables",
            )
            for name in mutator_names:
                mutator = getattr(manual_migrations, name)
                with self.assertRaises(StartupDatabaseModeError):
                    asyncio.run(mutator(AsyncMock()))

    def test_entrypoint_uses_central_mode_and_keeps_normal_path(self):
        source = (ROOT / "docker" / "backend-entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("StartupDatabaseMode.resolve()", source)
        self.assertIn('= "init"', source)
        self.assertIn("python init_db.py", source)
        self.assertIn("python run_migrations.py", source)
        self.assertIn("Startup database actions skipped.", source)

    def test_no_alembic_runtime_is_present(self):
        self.assertFalse((ROOT / "alembic.ini").exists())
        self.assertFalse((ROOT / "alembic").exists())

    def test_mode_cannot_be_controlled_by_request_or_database(self):
        source = (ROOT / "app" / "core" / "startup_database_mode.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("STARTUP_DATABASE_MODE")', source)
        self.assertNotIn("Request", source)
        self.assertNotIn("SELECT", source)


@unittest.skipUnless(BACKEND_RUNTIME_AVAILABLE, "backend dependencies are required")
class LifespanDatabaseModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_skip_blocks_lifespan_ddl_without_blocking_enabled_workers(self):
        from app.api import main

        fake_redis = MagicMock()
        fake_redis.close = AsyncMock()
        with patch.dict(os.environ, {
            "STARTUP_DATABASE_MODE": "skip",
            "FOLLOWUP_WORKER_ENABLED": "true",
            "CAMPAIGN_WORKER_ENABLED": "true",
        }, clear=False), \
             patch.object(main, "validate_security_settings"), \
             patch.object(main, "ensure_empresas_prompt_columns", new=AsyncMock()) as prompt_ddl, \
             patch.object(main.redis, "from_url", return_value=fake_redis), \
             patch.object(main.worker_runtime, "start_worker", return_value=None) as start_worker, \
             patch.object(main.worker_runtime, "stop_workers", new=AsyncMock()):
            async with main.lifespan(main.app):
                pass
        prompt_ddl.assert_not_awaited()
        self.assertEqual(start_worker.call_count, 2)
        self.assertTrue(all(call.kwargs["enabled"] for call in start_worker.call_args_list))
        fake_redis.close.assert_awaited_once()

    async def test_init_keeps_lifespan_database_actions_and_workers(self):
        from app.api import main

        fake_redis = MagicMock()
        fake_redis.close = AsyncMock()
        with patch.dict(os.environ, {
            "STARTUP_DATABASE_MODE": "init",
            "FOLLOWUP_WORKER_ENABLED": "true",
            "CAMPAIGN_WORKER_ENABLED": "true",
        }, clear=False), \
             patch.object(main, "validate_security_settings"), \
             patch.object(main, "ensure_empresas_prompt_columns", new=AsyncMock()) as prompt_ddl, \
             patch.object(main.redis, "from_url", return_value=fake_redis), \
             patch.object(main.worker_runtime, "start_worker", return_value=None) as start_worker, \
             patch.object(main.worker_runtime, "stop_workers", new=AsyncMock()):
            async with main.lifespan(main.app):
                pass
        prompt_ddl.assert_awaited_once()
        self.assertEqual(start_worker.call_count, 2)


class OperationalScriptTests(unittest.TestCase):
    def test_restart_script_is_backend_only_and_uses_skip(self):
        source = (ROOT / "scripts" / "restart_backend_without_db_actions.sh").read_text(encoding="utf-8")
        self.assertIn("STARTUP_DATABASE_MODE: skip", source)
        self.assertIn("--no-deps --force-recreate backend", source)
        self.assertNotIn("compose down", source)
        self.assertNotIn("docker compose config", source)
        self.assertIn("verify_startup_schema.py", source)
        self.assertIn("docker cp", source)
        self.assertIn("migration_meta_etapa2_applied=false", source)
        self.assertIn("HTTP_ROOT_OK", source)
        self.assertIn("OPENAPI_OK", source)
        self.assertIn("WEBSOCKET_OK", source)

    def test_schema_verifier_covers_schema_objects_and_migration_version(self):
        source = (ROOT / "scripts" / "verify_startup_schema.py").read_text(encoding="utf-8")
        for query_contract in ("pg_indexes", "pg_extension", "information_schema.sequences", "pg_enum"):
            self.assertIn(query_contract, source)
        self.assertIn("alembic_version|{alembic_version}", source)
