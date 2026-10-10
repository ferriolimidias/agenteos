from __future__ import annotations

"""Compatibilidade fina do worker de follow-up.

O fluxo único de follow-up vive em :mod:`app.services.followup_engine`.
Este módulo existe apenas para manter o ponto de entrada usado pelo bootstrap
da aplicação sem duplicar a implementação.
"""

from app.services.followup_engine import processar_followups_pendentes


__all__ = ["processar_followups_pendentes"]
