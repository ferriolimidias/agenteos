from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import BaseModel, ConfigDict

from app.leoft_generation.schemas import TrainingBlueprint
from app.services.leoft_generation_api_service import blueprint_hash_for_wire


class HermesDraftSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_number: int
    session_number: int
    objective: str
    phase_number: int = 1
    sequence: int
    session_code: str | None = None


class HermesTrainingBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "leoft.training-plan.v2"
    sessions: list[HermesDraftSession]


def _hermes_hash(wire: dict) -> str:
    data = HermesTrainingBlueprint.model_validate(wire).model_dump(mode="json")
    raw = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _wire_blueprint(
    *, count: int = 12, code_mode: str = "missing", explicit_phase: bool = False
) -> dict:
    sessions = []
    for sequence in range(1, count + 1):
        week, session = divmod(sequence - 1, 3)
        item = {
            "week_number": week + 1,
            "session_number": session + 1,
            "objective": (
                "Acentuação e mobilidade" if sequence == 1 else f"Sessão {sequence}"
            ),
            "sequence": sequence,
        }
        if explicit_phase:
            item["phase_number"] = 1 if week < 2 else 2
        if code_mode == "filled":
            item["session_code"] = f"W{week + 1:02d}S{session + 1:02d}"
        elif code_mode == "null":
            item["session_code"] = None
        elif code_mode == "mixed":
            item["session_code"] = (
                f"W{week + 1:02d}S{session + 1:02d}" if sequence % 2 else None
            )
        sessions.append(item)
    return {"schema_version": "leoft.training-plan.v2", "sessions": sessions}


@pytest.mark.parametrize(
    ("label", "wire"),
    [
        ("minimal", _wire_blueprint(count=1)),
        ("twelve_sessions", _wire_blueprint()),
        ("session_code_filled", _wire_blueprint(code_mode="filled")),
        ("session_code_none", _wire_blueprint(code_mode="null")),
        ("session_code_mixed", _wire_blueprint(code_mode="mixed")),
        ("unicode", _wire_blueprint(code_mode="null")),
        (
            "explicit_schema_version",
            {**_wire_blueprint(), "schema_version": "leoft.training-plan.v2"},
        ),
        ("default_schema_version", {"sessions": _wire_blueprint()["sessions"]}),
        ("explicit_phase_number", _wire_blueprint(explicit_phase=True)),
    ],
)
def test_agentos_hash_matches_faithful_hermes_algorithm(label: str, wire: dict) -> None:
    if label == "unicode":
        wire = {
            **wire,
            "sessions": [
                {**wire["sessions"][0], "objective": "Força e equilíbrio"},
                *wire["sessions"][1:],
            ],
        }
    blueprint = TrainingBlueprint.model_validate(wire)
    assert blueprint_hash_for_wire(blueprint) == _hermes_hash(wire), label


def test_agentos_persistence_normalizes_null_session_codes() -> None:
    wire = _wire_blueprint(code_mode="null")
    blueprint = TrainingBlueprint.model_validate(wire)
    assert all(item.session_code is None for item in blueprint.sessions)


def test_agentos_hash_is_independent_of_wire_key_order() -> None:
    wire = _wire_blueprint(count=1)
    reordered = {
        "sessions": [
            {
                "objective": wire["sessions"][0]["objective"],
                "sequence": wire["sessions"][0]["sequence"],
                "session_number": wire["sessions"][0]["session_number"],
                "week_number": wire["sessions"][0]["week_number"],
            }
        ],
        "schema_version": wire["schema_version"],
    }
    assert blueprint_hash_for_wire(
        TrainingBlueprint.model_validate(wire)
    ) == _hermes_hash(reordered)
