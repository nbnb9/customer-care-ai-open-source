from app.shared.config import get_settings

from .base import FakeLLMGateway, LLMGateway
from .openai_compat import OpenAICompatibleGateway


def get_gateway() -> LLMGateway:
    """按配置返回 Fake 或真实网关；无 key 时永远回退 Fake，保证离线可用。"""
    settings = get_settings()
    if settings.llm_provider == "fake" or not settings.llm_api_key:
        return FakeLLMGateway()
    return OpenAICompatibleGateway(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        provider=settings.llm_provider,
    )
