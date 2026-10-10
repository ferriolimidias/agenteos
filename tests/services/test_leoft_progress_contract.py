import unittest
from pathlib import Path

from app.services.leoft_progress_service import _canonical_exercise_id, _resolve_exercise


ROOT = Path(__file__).resolve().parents[2]


class LeoFTProgressContractTests(unittest.TestCase):
    def test_migration_is_reversible_and_has_required_constraints(self):
        migration = (ROOT / "db/migrations/2026_07_29_002_leoft_student_progress.sql").read_text()
        rollback = (ROOT / "db/migrations/2026_07_29_002_leoft_student_progress_rollback.sql").read_text()
        self.assertIn("CREATE TABLE IF NOT EXISTS leoft_student_exercise_progress", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS leoft_student_workout_progress", migration)
        self.assertIn("UNIQUE (access_id, plan_id, exercise_id)", migration)
        self.assertIn("UNIQUE (access_id, plan_id, workout_id)", migration)
        self.assertIn("DROP TABLE IF EXISTS leoft_student_workout_progress", rollback)
        self.assertIn("DROP TABLE IF EXISTS leoft_student_exercise_progress", rollback)

    def test_repeated_source_ids_get_canonical_per_workout(self):
        workouts = {"w01": {"w01:agachamento"}, "w02": {"w02:agachamento"}}
        self.assertEqual(_canonical_exercise_id("agachamento", "w01", workouts), "w01:agachamento")
        self.assertEqual(_resolve_exercise("agachamento", {}, workouts), ("w01", "w01:agachamento"))

    def test_public_router_contains_exact_progress_methods(self):
        source = (ROOT / "app/api/routers/leoft_student_app.py").read_text()
        for method in ("@router.get(\"/progress\")", "@router.post(\"/progress/exercises/{exercise_id}/complete\")", "@router.delete(\"/progress/exercises/{exercise_id}/complete\")", "@router.post(\"/progress/workouts/{workout_id}/complete\")", "@router.delete(\"/progress/workouts/{workout_id}/complete\")"):
            self.assertIn(method, source)


if __name__ == "__main__":
    unittest.main()
