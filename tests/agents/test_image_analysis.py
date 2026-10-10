import asyncio

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage
from app.agents.vision.image_analysis import ImageAnalysisResult, ImageAnalysisService, MockImageAnalysisProvider


def test_mock_image_analysis_reaches_agent_response():
    result = ImageAnalysisResult(
        available=True,
        image_detected=True,
        summary="um objeto para avaliação",
        objects=["objeto"],
        confidence=0.8,
        status="available",
        provider="mock",
    )
    service = ImageAnalysisService(
        provider=MockImageAnalysisProvider(result),
        config_override={"enabled": True, "vision_provider": "mock"},
    )
    agent = AtendimentoAgent(image_analysis_service=service)
    output = asyncio.run(
        agent.run(
            AtendimentoAgentInput(
                state=ConversationState(empresa_id="tenant-test"),
                messages=[
                    IncomingMessage(
                        text="[Imagem]",
                        tenant_id="tenant-test",
                        media_type="image",
                        media_url="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
                        media_mime_type="image/jpeg",
                        has_image=True,
                    )
                ],
            )
        )
    )

    assert output.response_text
    assert output.guard_result.allowed is True
    assert output.specialist_result is not None
