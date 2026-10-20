"""Клиент к OpenAI-совместимым чат-эндпоинтам локальных моделей (LM Studio, vLLM)."""

from pelican.llm.client import Breaker, LLMClient, LLMError, LLMTruncated

__all__ = ["Breaker", "LLMClient", "LLMError", "LLMTruncated"]
