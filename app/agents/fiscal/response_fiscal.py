from __future__ import annotations

import json
import re
from typing import Any, TypedDict


class FiscalFinding(TypedDict):
    code: str
    severity: str
    message: str


class AgenteFiscal:
    TECHNICAL_TERMS = (
        "Intent" + "Router",
        "Specialist" + "Result",
        "Tool" + "Registry",
        "Conversation" + "State",
        "sk-",
        "Bearer",
    )
    INTERMEDIATE_PHRASES = ("vou calcular", "vou verificar", "vou consultar")
    FIELD_LABELS = {
        "measure": ("medida", "tamanho", "metragem", "metros", "metro"),
        "tipo_de_servico": ("serviço", "tipo de serviço"),
        "service_type": ("serviço", "tipo de serviço"),
        "nome": ("nome",),
        "endereco_ou_bairro": ("endereço", "bairro"),
        "melhor_dia": ("dia", "data"),
        "melhor_turno": ("turno", "horário", "horario"),
        "telefone": ("telefone", "whatsapp", "contato"),
        "servico_desejado": ("serviço", "servico"),
    }

    def validate(
        self,
        *,
        response: str,
        tool_results: dict,
        memory: dict,
        tenant_config: dict,
    ) -> list[FiscalFinding]:
        text = str(response or "").strip()
        findings: list[FiscalFinding] = []
        lower = text.lower()
        orcamento = tool_results.get("orcamento") if isinstance(tool_results, dict) else None
        orcamento = orcamento if isinstance(orcamento, dict) else {}
        estimate = orcamento.get("estimate") if isinstance(orcamento.get("estimate"), dict) else {}
        amount = estimate.get("amount")
        missing_fields = [str(item) for item in orcamento.get("missing_fields") or [] if str(item or "").strip()]

        if amount is not None:
            amount_tokens = self._amount_tokens(amount)
            if not any(token and token in lower for token in amount_tokens):
                findings.append(self._finding("budget_amount_missing", "error", "Resposta nao menciona o valor calculado pela tool."))
            if self._has_only_intermediate_phrase(lower):
                findings.append(self._finding("budget_inconclusive", "error", "Resposta ficou em promessa intermediaria apesar de haver estimativa."))
        else:
            if self._mentions_price(lower):
                findings.append(self._finding("price_invented", "error", "Resposta menciona preco sem estimativa estruturada."))

        if missing_fields:
            if self._mentions_price(lower):
                findings.append(self._finding("missing_fields_with_price", "error", "Resposta inventou preco quando ainda faltam campos."))
            if not any(self._field_is_asked(lower, field) for field in missing_fields):
                findings.append(self._finding("missing_field_not_asked", "error", "Resposta nao perguntou os campos faltantes informados pela tool."))
            for known_field in self.FIELD_LABELS:
                if known_field not in missing_fields and self._field_is_asked(lower, known_field):
                    findings.append(self._finding("extra_missing_field_asked", "error", "Resposta pediu campo fora de missing_fields."))
                    break

        if any(phrase in lower for phrase in self.INTERMEDIATE_PHRASES) and not self._has_objective_question(lower) and amount is None:
            findings.append(self._finding("inconclusive_intermediate_phrase", "error", "Resposta contem promessa intermediaria sem conclusao ou pergunta objetiva."))

        for token in self.TECHNICAL_TERMS:
            if token.lower() in lower:
                findings.append(self._finding("technical_leak", "error", f"Resposta vazou termo tecnico: {token}."))
                break

        if self._looks_like_raw_json(text):
            findings.append(self._finding("raw_json_leak", "error", "Resposta parece conter JSON bruto."))

        return findings

    def _amount_tokens(self, amount: Any) -> list[str]:
        raw = str(amount).strip().lower()
        tokens = [raw]
        try:
            value = float(amount)
            if value.is_integer():
                tokens.extend([str(int(value)), f"r$ {int(value)}", f"r${int(value)}"])
            tokens.extend([f"{value:.2f}".replace(".", ","), f"{value:.2f}"])
        except (TypeError, ValueError):
            pass
        return tokens

    def _mentions_price(self, lower: str) -> bool:
        return bool(re.search(r"\br\$\s*\d+|\b\d+[,.]?\d*\s*reais\b|\bvalor\b|\bpre[cç]o\b", lower))

    def _field_is_asked(self, lower: str, field: str) -> bool:
        labels = self.FIELD_LABELS.get(field, (field.replace("_", " "),))
        return "?" in lower and any(label in lower for label in labels)

    def _has_objective_question(self, lower: str) -> bool:
        return "?" in lower or any(term in lower for term in ("me envie", "me manda", "informe", "preciso de"))

    def _has_only_intermediate_phrase(self, lower: str) -> bool:
        return any(phrase in lower for phrase in self.INTERMEDIATE_PHRASES) and not self._mentions_price(lower)

    def _looks_like_raw_json(self, text: str) -> bool:
        stripped = text.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            return False
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            return False
        return True

    def _finding(self, code: str, severity: str, message: str) -> FiscalFinding:
        return {"code": code, "severity": severity, "message": message}
