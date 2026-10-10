from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.graph import NewAgentGraph, run_new_agent_flow
from app.agents.guards.response_guard import ResponseGuard
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage
from app.agents.state.builder import build_conversation_state
from app.agents.vision.image_analysis import (
    ImageAnalysisRequest,
    ImageAnalysisResult,
    ImageAnalysisService,
    MockImageAnalysisProvider,
)


DATA_IMAGE = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w=="


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main() -> int:
    failures: list[str] = []

    try:
        incoming = IncomingMessage(
            text="segue imagem",
            tenant_id="tenant-mock",
            channel="whatsapp",
            media_type="image",
            media_url=DATA_IMAGE,
            media_id="media-123",
            media_mime_type="image/jpeg",
            caption="foto para avaliação",
            has_image=True,
        )
        assert_true(incoming.has_image is True, "IncomingMessage não aceitou has_image")
        assert_true(incoming.caption == "foto para avaliação", "IncomingMessage não preservou caption")
        print("OK - IncomingMessage aceita imagem com caption")
    except Exception as exc:
        failures.append(f"FAIL - IncomingMessage: {exc}")

    try:
        state = build_conversation_state({"empresa_id": "tenant-mock"}, incoming=incoming)
        assert_true(state.has_image is True, "ConversationState não preservou has_image")
        assert_true(state.media_mime_type == "image/jpeg", "ConversationState não preservou mime")
        assert_true(state.caption == "foto para avaliação", "ConversationState não preservou caption")
        print("OK - ConversationState preserva metadados de imagem")
    except Exception as exc:
        failures.append(f"FAIL - ConversationState: {exc}")

    mock_result = ImageAnalysisResult(
        available=True,
        image_detected=True,
        summary="um sofá claro com sinais de uso",
        objects=["sofa"],
        scene="ambiente interno",
        condition="uso moderado",
        confidence=0.78,
        warnings=["A análise visual é aproximada e precisa de confirmação humana."],
        needs_human_confirmation=True,
        status="available",
        provider="mock",
    )
    service = ImageAnalysisService(
        provider=MockImageAnalysisProvider(mock_result),
        config_override={"enabled": True, "vision_provider": "mock"},
    )

    try:
        result = await service.analyze(
            ImageAnalysisRequest(
                tenant_id="tenant-mock",
                media_url=DATA_IMAGE,
                media_mime_type="image/jpeg",
                caption="foto para avaliação",
            )
        )
        assert_true(result.available is True, "MockImageAnalysisProvider não retornou available")
        assert_true("sofa" in result.objects, "MockImageAnalysisProvider não retornou objetos")
        print("OK - MockImageAnalysisProvider retorna análise estruturada")
    except Exception as exc:
        failures.append(f"FAIL - Mock provider: {exc}")

    try:
        agent = AtendimentoAgent(image_analysis_service=service)
        output = await agent.run(AtendimentoAgentInput(state=ConversationState(empresa_id="tenant-mock"), messages=[incoming]))
        assert_true(output.response_text, "AtendimentoAgent não respondeu com imagem")
        assert_true("imagem" in output.response_text.lower() or "sofá" in output.response_text.lower(), "Resposta não usou imagem como apoio")
        assert_true(output.guard_result and output.guard_result.allowed, "ResponseGuard bloqueou resposta segura")
        print("OK - AtendimentoAgent usa análise mockada como apoio")
    except Exception as exc:
        failures.append(f"FAIL - AtendimentoAgent mock: {exc}")

    try:
        fallback_output = await run_new_agent_flow(
            {
                "empresa_id": "tenant-sem-config",
                "mensagem_atual": "[Imagem]",
                "has_image": True,
                "media_type": "image",
                "media_url": DATA_IMAGE,
                "media_mime_type": "image/jpeg",
                "caption": "foto",
            }
        )
        assert_true(fallback_output.response_text, "Fallback sem análise não respondeu")
        assert_true("recebi" in fallback_output.response_text.lower(), "Fallback não confirmou recebimento")
        print("OK - análise indisponível usa fallback seguro")
    except Exception as exc:
        failures.append(f"FAIL - fallback indisponível: {exc}")

    try:
        graph = NewAgentGraph(AtendimentoAgent(image_analysis_service=service))
        graph_state = await graph.ainvoke(
            {
                "empresa_id": "tenant-mock",
                "has_image": True,
                "media_type": "image",
                "media_url": DATA_IMAGE,
                "media_mime_type": "image/jpeg",
                "caption": "foto para avaliação",
            }
        )
        assert_true(graph_state.get("agent_architecture") == "app.agents", "graph.ainvoke não usou app.agents")
        assert_true(graph_state.get("has_image") is True, "graph.ainvoke não preservou has_image")
        assert_true((graph_state.get("image_analysis") or {}).get("available") is True, "graph.ainvoke não anexou análise")
        print("OK - graph.ainvoke recebe state com imagem e não quebra")
    except Exception as exc:
        failures.append(f"FAIL - graph.ainvoke imagem: {exc}")

    try:
        blocked = ResponseGuard().validate("token sk-1234567890abcdef")
        assert_true(blocked.allowed is False and blocked.sanitized is True, "ResponseGuard não bloqueou segredo")
        print("OK - ResponseGuard continua bloqueando segredo")
    except Exception as exc:
        failures.append(f"FAIL - ResponseGuard: {exc}")

    if failures:
        print("\nFalhas:")
        for failure in failures:
            print(failure)
        return 1
    print("\nTodos os testes de image analysis passaram sem OpenAI, Evolution ou API externa real.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
