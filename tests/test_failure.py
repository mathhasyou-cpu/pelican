"""Отказ прогона словами пользователя: фраза без кода и JSON, с действием."""

from __future__ import annotations

import httpx
import psycopg
import pytest

from pelican.failure import explain
from pelican.llm.client import LLMError

REQ = httpx.Request("POST", "http://host.docker.internal:1234/v1/chat/completions")
NO_MODEL = (
    'HTTP 400: {"error": {"message": "No models loaded. Please load a model in the developer '
    "page or use the 'lms load' command.\", \"type\": \"invalid_request_error\"}}"
)


@pytest.mark.parametrize(
    ("exc", "says"),
    [
        (LLMError(NO_MODEL), "модель на нём не загружена"),
        (LLMError("HTTP 401: unauthorized"), "отклонил запрос"),
        (LLMError("не JSON вопреки схеме: ..."), "не по схеме"),
        (httpx.ConnectError("refused", request=REQ), "не достучался"),
        (httpx.ReadTimeout("slow", request=REQ), "не ответил вовремя"),
        (httpx.RemoteProtocolError("eof", request=REQ), "ответил ошибкой"),
        (psycopg.OperationalError("connection refused"), "База данных"),
        (KeyError("signals"), "внутренней ошибке"),
    ],
)
def test_explain_names_the_failure_without_raw_text(exc, says):
    text = explain(exc)
    assert says in text
    # Ни JSON, ни имени исключения: сырой текст уходит в `error_detail`.
    assert "{" not in text and type(exc).__name__ not in text
    assert "Повторите" in text or "повторить" in text
