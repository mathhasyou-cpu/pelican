"""Отказ прогона словами пользователя.

Задание хранит две строки, как problem details (RFC 9457): `error` — что случилось и что
делать, для человека, без кода и JSON; `error_detail` — сырой текст исключения для
администратора стенда. Интерфейсы показывают первую, вторую прячут под «Подробности».
Разбор по типу исключения, а не по тексту, кроме одного случая: LM Studio без
загруженной модели отвечает 400, и отличить его можно только по сообщению.
"""

from __future__ import annotations

import httpx
import psycopg

from pelican.llm.client import LLMError

#: Как LM Studio и OpenAI-совместимые серверы говорят «модели нет».
NO_MODEL = ("No models loaded", "model_not_found", "model not found")

RETRY = "Повторите запрос"
ADMIN = "если отказ повторится, передайте подробности администратору стенда"


def explain(exc: BaseException) -> str:
    """Фраза для пользователя: что сломалось и что с этим делать."""
    text = str(exc)
    if isinstance(exc, LLMError):
        if any(s in text for s in NO_MODEL):
            return ("Сервер моделей запущен, но модель на нём не загружена, поэтому прогон "
                    "остановился. Запрос можно повторить, когда администратор стенда загрузит модель.")
        if text.startswith("HTTP "):
            return f"Сервер моделей отклонил запрос, и прогон остановился. {RETRY}; {ADMIN}."
        return f"Модель ответила не по схеме, и прогон остановился. {RETRY}: такой сбой обычно разовый."
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return ("Стенд не достучался до сервера моделей или поиска по корпусу, и прогон "
                f"остановился. {RETRY} позже; {ADMIN}.")
    if isinstance(exc, httpx.TimeoutException):
        return f"Сервис стенда не ответил вовремя, и прогон остановился. {RETRY} позже; {ADMIN}."
    if isinstance(exc, httpx.HTTPError):
        return f"Сервис стенда ответил ошибкой, и прогон остановился. {RETRY} позже; {ADMIN}."
    if isinstance(exc, psycopg.OperationalError):
        return f"База данных стенда недоступна, и прогон остановился. {RETRY} позже; {ADMIN}."
    return f"Прогон остановился на внутренней ошибке. {RETRY}; {ADMIN}."
