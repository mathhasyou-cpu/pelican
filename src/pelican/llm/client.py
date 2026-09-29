"""Один клиент на локальные модели: LM Studio и vLLM говорят на одном
OpenAI-совместимом протоколе, поэтому это класс с разными конфигами, а не два
адаптера. Переезд на vLLM ради батчинга — смена `LLM_BASE_URL`.

Ответ всегда structured output (`response_format.json_schema`, тот же формат,
что у OpenAI Structured Outputs). 12B без схемы ломает JSON заметно чаще, чем
терпимо. Схема гарантирует форму, но не смысл: содержимое приходит **строкой**
в `choices[0].message.content`, и валидировать его всё равно обязан вызывающий.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class LLMError(RuntimeError):
    """Ответ пришёл, но пользоваться им нельзя: 4xx, не-JSON, пустой выбор."""


# Ретраим только сеть и 5xx.
#
# ⚠️ ReadTimeout сюда не входит намеренно. Сервер выполняет запросы строго по
# одному, а очередь ему набивает наш пул воркеров: истёкший таймаут означает
# «очередь длиннее, чем мы рассчитали», и повтор добьёт ровно ту очередь, из
# которой мы пытаемся выжать пропускную способность. Такой запрос честнее
# уронить и переработать сущности следующим прогоном — стадия инкрементальна.
RETRYABLE = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.HTTPStatusError,
)

with_retries = retry(
    retry=retry_if_exception_type(RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)


# ── Предохранитель: модели нет вовсе ──────────────────────────────────────

# ⚠️ Это НЕ «модель занята». Занятая очередь LM Studio отдаёт таймаут или 5xx,
# и повторить такой пакет правильно — очередь разгребётся. `ConnectError` же
# означает, что соединения не случилось: процесса на том конце нет, и следующие
# триста пакетов ответят тем же.
UNREACHABLE = (httpx.ConnectError, httpx.ConnectTimeout)

# Три подряд — канонический стартовый порог для circuit breaker (Nygard,
# «Release It!», 2007). Каждый пакет к этому моменту уже израсходовал свои три
# попытки tenacity, то есть девять отказов подряд на установку соединения.
BREAKER_STREAK = 3


@dataclass(slots=True)
class Breaker:
    """Circuit breaker: N подряд отказов соединения — стадию не продолжаем.

    Приём канонический (Nygard, «Release It!»): состояние OPEN означает
    fail-fast — не ходить туда, где уже трижды не открылось соединение.
    Замер, ради которого он здесь: за сутки планировщик 36 раз прогнал `mine`
    по 245 обречённых пакетов, по 12–14 минут каждый — восемь часов холостых
    попыток при выключенной машине с моделью.

    ⚠️ Расхождение с каноном намеренное: состояний HALF_OPEN и таймера
    восстановления здесь нет. Стадия живёт минуты и инкрементальна, а роль
    «попробовать снова позже» играет планировщик, который вызовет её через
    свой интервал (`docs/scheduling.md`). Держать таймер внутри процесса,
    который всё равно завершится, значило бы городить состояние на пустом
    месте.

    Счётчик общий на стадию, а не на воркер: пакеты идут конвейером в
    несколько потоков, и порознь каждый досчитал бы до порога втрое дольше.
    """

    limit: int = BREAKER_STREAK
    streak: int = 0
    tripped: bool = False

    def record(self, exc: BaseException | None) -> bool:
        """Учесть исход пакета. `True` — предохранитель сработал, стадию рвём.

        Успех и любой другой отказ обнуляют серию: ошибка разбора или занятая
        очередь ничего не говорят о доступности модели.
        """
        if not isinstance(exc, UNREACHABLE):
            self.streak = 0
            return False
        self.streak += 1
        self.tripped = self.streak >= self.limit
        return self.tripped


@dataclass(slots=True)
class LLMClient:
    base_url: str
    api_key: str
    model: str
    timeout_s: float = 60.0
    # "none" отключает reasoning-токены. Для пакетной разметки это не тюнинг, а
    # разница в порядок: gemma-4 тратит на рассуждение ~90% токенов ответа и на
    # большом пакете не доходит до ответа вовсе. См. docs/llm-setup.md.
    reasoning_effort: str | None = "none"
    max_tokens: int | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _body(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        name: str,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Классификация должна быть воспроизводимой: один и тот же вход —
            # один и тот же ярлык, иначе замеры порогов не с чем сравнивать.
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        }
        if self.reasoning_effort is not None:
            body["reasoning_effort"] = self.reasoning_effort
        # Потолок задаётся на вызов, а не на клиента: в пакетных стадиях ответ
        # повторяет вход эхом, поэтому единственно верного числа для всех
        # пакетов не существует. См. docs/llm-setup.md.
        cap = max_tokens if max_tokens is not None else self.max_tokens
        if cap is not None:
            body["max_tokens"] = cap
        return body

    async def json_completion(
        self,
        client: httpx.AsyncClient,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        name: str = "response",
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Один вызов чата со схемой. Возвращает распарсенный объект.

        `client` передаётся снаружи: пул воркеров переиспользует одно соединение,
        а создание AsyncClient на запрос сводило бы конвейеризацию на нет.
        """
        response = await self._post(client, self._body(system, user, schema, name, max_tokens))
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise LLMError(f"пустой ответ модели: {json.dumps(payload)[:300]}")
        content = choices[0].get("message", {}).get("content") or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"не JSON вопреки схеме: {content[:300]}") from exc
        if not isinstance(parsed, dict):
            raise LLMError(f"ожидался объект, пришло {type(parsed).__name__}")
        return parsed

    @with_retries
    async def _post(self, client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
        response = await client.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            json=body,
            headers=self._headers(),
            timeout=httpx.Timeout(self.timeout_s),
        )
        if response.status_code >= 500 or (
            # ⚠️ LM Studio отдаёт сбой своего движка кодом 400 («Engine protocol predict
            # request failed: fetch failed») — это 5xx по смыслу, и один такой ответ
            # ронял двухчасовой прогон; следующий запрос через секунду проходит.
            response.status_code == 400 and "predict request failed" in response.text
        ):
            response.raise_for_status()  # уйдёт в ретрай
        if response.is_error:
            # 4xx — ошибка в нашем запросе или в ключах; повтор её не вылечит.
            raise LLMError(f"HTTP {response.status_code}: {response.text[:300]}")
        return response
