"""Regression checks for the canonical SQLAlchemy model registry."""

from db.database import Base
import db.models as models
import db.leoft_models as leoft_models


def test_shared_tables_are_registered_once_on_canonical_base():
    for table_name in ("leoft_training_plans", "agente_ferramentas"):
        matches = [table for table in Base.metadata.tables.values() if table.name == table_name]
        assert len(matches) == 1
        assert matches[0].metadata is Base.metadata


def test_leoft_models_are_reexported_from_canonical_module():
    assert models.LeoFTTrainingPlan is leoft_models.LeoFTTrainingPlan
    assert models.LeoFTTrainingPlan.__module__ == "db.leoft_models"
