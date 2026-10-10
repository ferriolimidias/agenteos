from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch

from app.core.startup_database_mode import StartupDatabaseMode, StartupDatabaseModeError


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ROOT = _REPOSITORY_ROOT if (_REPOSITORY_ROOT / "docker").exists() else Path("/app")
ENTRYPOINT = ROOT / "docker" / "backend-entrypoint.sh"


class StartupModeContractTests(unittest.TestCase):
    def test_allowed_modes_and_historical_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(StartupDatabaseMode.resolve(), StartupDatabaseMode.INIT)
        self.assertEqual(StartupDatabaseMode.resolve("init"), StartupDatabaseMode.INIT)
        self.assertEqual(StartupDatabaseMode.resolve("migrate"), StartupDatabaseMode.MIGRATE)
        self.assertEqual(StartupDatabaseMode.resolve(" skip "), StartupDatabaseMode.SKIP)

    def test_invalid_mode_fails_closed(self):
        with self.assertRaisesRegex(StartupDatabaseModeError, "init, migrate, skip"):
            StartupDatabaseMode.resolve("unknown")
        with self.assertRaises(StartupDatabaseModeError):
            StartupDatabaseMode.resolve("")

    def test_mode_permissions(self):
        self.assertTrue(StartupDatabaseMode.INIT.allows_manual_startup_migrations)
        self.assertFalse(StartupDatabaseMode.MIGRATE.allows_manual_startup_migrations)
        self.assertFalse(StartupDatabaseMode.SKIP.allows_manual_startup_migrations)
        self.assertTrue(StartupDatabaseMode.INIT.allows_runtime_workers)
        self.assertTrue(StartupDatabaseMode.MIGRATE.allows_runtime_workers)
        self.assertTrue(StartupDatabaseMode.SKIP.allows_runtime_workers)

    def test_entrypoint_modes_call_only_their_allowed_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory)
            calls = fake_bin / "calls.log"
            (fake_bin / "python").write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-c\" ]; then echo \"${STARTUP_DATABASE_MODE:-init}\"; elif [ \"$1\" = \"-\" ]; then exit 0; else echo python:$* >> \"$CALLS\"; fi\n",
                encoding="utf-8",
            )
            (fake_bin / "uvicorn").write_text(
                "#!/bin/sh\necho uvicorn >> \"$CALLS\"\n",
                encoding="utf-8",
            )
            for executable in ("python", "uvicorn"):
                (fake_bin / executable).chmod(0o755)

            def run(mode: str) -> list[str]:
                calls.unlink(missing_ok=True)
                environment = {"PATH": str(fake_bin), "CALLS": str(calls), "STARTUP_DATABASE_MODE": mode}
                subprocess.run(["/bin/sh", str(ENTRYPOINT)], env=environment, check=True, capture_output=True, text=True)
                return calls.read_text(encoding="utf-8").splitlines()

            self.assertEqual(run("skip"), ["uvicorn"])
            self.assertEqual(run("migrate"), ["python:run_migrations.py", "uvicorn"])
            self.assertEqual(run("init"), ["python:init_db.py", "python:run_migrations.py", "uvicorn"])

            calls.unlink(missing_ok=True)
            invalid_environment = {"PATH": str(fake_bin), "CALLS": str(calls), "STARTUP_DATABASE_MODE": "invalid"}
            result = subprocess.run(["/bin/sh", str(ENTRYPOINT)], env=invalid_environment, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(calls.exists())

    def test_entrypoint_is_fail_closed_and_forwards_signals(self):
        source = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("set -eu", source)
        self.assertIn("STARTUP_DATABASE_MODE", source)
        self.assertIn("Invalid startup database mode", source)
        self.assertIn("exec uvicorn", source)
        self.assertNotIn("sqlite3", source)

    def test_no_sqlite_dependency_in_startup_surface(self):
        for path in (ROOT / "app" / "core" / "startup_database_mode.py", ENTRYPOINT):
            self.assertNotIn("sqlite3", path.read_text(encoding="utf-8"))
            self.assertNotIn("catalog.sqlite", path.read_text(encoding="utf-8"))


BACKEND_DEPENDENCIES = all(
    importlib.util.find_spec(name) is not None for name in ("fastapi", "sqlalchemy", "redis")
)


@unittest.skipUnless(BACKEND_DEPENDENCIES, "backend dependencies are required")
class LifespanStartupModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_skip_lifespan_skips_database_mutators_but_starts_enabled_workers(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.api import main

        fake_redis = MagicMock()
        fake_redis.close = AsyncMock()
        with patch.dict(os.environ, {
            "STARTUP_DATABASE_MODE": "skip",
            "FOLLOWUP_WORKER_ENABLED": "true",
            "CAMPAIGN_WORKER_ENABLED": "true",
        }, clear=False), \
             patch.object(main, "validate_security_settings"), \
             patch.object(main, "ensure_empresas_prompt_columns", new=AsyncMock()) as mutator, \
             patch.object(main.redis, "from_url", return_value=fake_redis), \
             patch.object(main.worker_runtime, "start_worker", return_value=None) as start_worker, \
             patch.object(main.worker_runtime, "stop_workers", new=AsyncMock()):
            async with main.lifespan(main.app):
                pass
        mutator.assert_not_awaited()
        self.assertEqual([call.kwargs["name"] for call in start_worker.call_args_list], ["followup", "campaign"])
        self.assertTrue(all(call.kwargs["enabled"] for call in start_worker.call_args_list))
        fake_redis.close.assert_awaited_once()

    async def test_init_lifespan_preserves_database_mutator_and_worker(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.api import main

        fake_redis = MagicMock()
        fake_redis.close = AsyncMock()
        with patch.dict(os.environ, {"STARTUP_DATABASE_MODE": "init"}, clear=False), \
             patch.dict(os.environ, {"FOLLOWUP_WORKER_ENABLED": "true", "CAMPAIGN_WORKER_ENABLED": "true"}, clear=False), \
             patch.object(main, "validate_security_settings"), \
             patch.object(main, "ensure_empresas_prompt_columns", new=AsyncMock()) as mutator, \
             patch.object(main.redis, "from_url", return_value=fake_redis), \
             patch.object(main.worker_runtime, "start_worker", return_value=None) as start_worker, \
             patch.object(main.worker_runtime, "stop_workers", new=AsyncMock()):
            async with main.lifespan(main.app):
                pass
        mutator.assert_awaited_once()
        self.assertEqual(start_worker.call_count, 2)
        fake_redis.close.assert_awaited_once()

    async def test_startup_worker_flag_matrix_is_independent_from_database_mode(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.api import main

        scenarios = (
            ("skip", "true", "true", [True, True]),
            ("migrate", "true", "true", [True, True]),
            ("skip", "false", "false", [False, False]),
            ("init", "true", "false", [True, False]),
        )
        for mode, followup, campaign, expected in scenarios:
            with self.subTest(mode=mode, followup=followup, campaign=campaign):
                fake_redis = MagicMock()
                fake_redis.close = AsyncMock()
                with patch.dict(os.environ, {
                    "STARTUP_DATABASE_MODE": mode,
                    "FOLLOWUP_WORKER_ENABLED": followup,
                    "CAMPAIGN_WORKER_ENABLED": campaign,
                }, clear=False), \
                     patch.object(main, "validate_security_settings"), \
                     patch.object(main, "ensure_empresas_prompt_columns", new=AsyncMock()) as mutator, \
                     patch.object(main.redis, "from_url", return_value=fake_redis), \
                     patch.object(main.worker_runtime, "start_worker", return_value=None) as start_worker, \
                     patch.object(main.worker_runtime, "stop_workers", new=AsyncMock()):
                    async with main.lifespan(main.app):
                        pass
                self.assertEqual([call.kwargs["enabled"] for call in start_worker.call_args_list], expected)
                if mode == "init":
                    mutator.assert_awaited_once()
                else:
                    mutator.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
