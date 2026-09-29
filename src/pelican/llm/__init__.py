"""Клиент к OpenAI-совместимым чат-эндпоинтам локальных моделей (LM Studio, vLLM)."""

from pelican.llm.client import Breaker, LLMClient, LLMError

__all__ = ["Breaker", "LLMClient", "LLMError"]
