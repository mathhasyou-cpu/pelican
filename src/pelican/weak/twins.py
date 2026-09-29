"""Сведение названий-близнецов моделью: одно ли направление названо двумя строками.

## Почему не порог

Замер на ТОП восьми отчётов истории: ни слова, ни векторы дубль от соседа не отделяют.
«automated kyc and kyb compliance infrastructure» / «automated kyc and aml workflows» —
одно направление, Жаккар значимых слов 0.29 и косинус `embedding-gemma` 0.794; а разные
«gpu inference optimization and orchestration» / «hardware-accelerated inference engines»
стоят выше — 0.810, «mosquito trapping» / «genetic mosquito control» — 0.790. Ось не та,
и решение вынесено на модель.

## Приём: entity matching на LLM, фильтр + попарное сопоставление

Wang et al., *Match, Compare, or Select?* (COLING 2025): дешёвый фильтр сужает кандидатов
(здесь косинус ярлыков, `BLOCK_COS`, `K_SELECT`), и дальше решает модель. Голова
предъявляется ПО ОДНОЙ, ближайшая первой, до первого «да». Это их стратегия «matching».

⚠️ **Не наступить: «selecting» (список голов в одном вызове) здесь не работает**, хотя в
статье у 6 из 8 открытых моделей он лучший. Статья предполагает одно верное совпадение
на запись, а у нас их бывает несколько. Замер на `gemma-4-12b-qat`: «automated kyc and
kyb compliance infrastructure» при одной голове «automated kyb and compliance for
financial institutions» сводится в обе стороны. Добавь вторую подходящую голову («automated
kyc and aml verification systems») — модель отвечает «ни одна» при любом порядке, и в
живом прогоне дубль вернулся в ТОП.
Правила сопоставления словами в промпте, а не примеры: у открытых моделей правила дают
+3–17 F1, примеры маленьким моделям вредят (Peeters, Steiner, Bizer, *Entity Matching
using Large Language Models*, EDBT 2025, §4.1–4.2).

⚠️ **Кандидат выбирает только среди уже принятых ГОЛОВ групп**, а не среди всех
предыдущих: независимые попарные вердикты, сведённые транзитивно, сцепляют цепочку
A~B, B~C при A≁C (там же, «global consistency»). Это та же жадная схема, что у
`ask._merge_near`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import numpy as np

from pelican.config import settings
from pelican.llm import LLMClient, LLMError

#: Косинус ярлыков, ниже которого голова в выбор не попадает. Задача фильтра — полнота:
#: все пары-дубли необанка (0.752–0.854) обязаны пройти. ⚠️ Догадка (docs/todo.md).
BLOCK_COS = 0.70
#: Сколько ближайших голов предъявляется по очереди — потолок вызовов на кандидата. ⚠️ Догадка.
K_SELECT = 4

SYSTEM_PROMPT = """You deduplicate a list of technology directions shown to an analyst.
You get one RECORD and numbered OPTIONS. Answer with the number of the option that names
the SAME direction as the record, or 0 if none does.

SAME direction — the analyst would read the second card as a repeat of the first:
  the same kind of product or technique solving the same problem for the same buyer,
  differing only in wording, in breadth of phrasing, or in which sub-functions of one
  product are listed ("fraud detection for card payments" and "real-time payment fraud
  prevention with machine learning").
DIFFERENT direction — the underlying technology or mechanism differs, even within one
  field and one buyer ("solid-state batteries" and "battery recycling"; "satellite
  imagery analytics" and "satellite launch services").

Sharing a field, a buyer or a buzzword ("AI", "agentic", "automated") does not make two
directions the same. If unsure, answer 0."""

#: Выбор одной записи: (запись, варианты) → номер варианта с 1 или 0.
Pick = Callable[[str, list[str]], Awaitable[int]]


def options(
    vectors: np.ndarray, heads: list[int], i: int, keys: list[str] | None = None
) -> list[int]:
    """Головы для выбора: с тем же ключом (ядром) или похожие не ниже `BLOCK_COS`.

    Головы с общим ядром идут первыми, дальше — по близости, не больше `K_SELECT`.
    ⚠️ Общее ядро — повод СПРОСИТЬ, а не свести: ядро зависит от соседей по пачке
    `core.cores`, и в одном прогоне «agentic ai for financial crime and compliance» и
    «decentralized identity and kyc frameworks» получили общее «KYC compliance» с
    KYC-комплаенсом — строковое сведение склеило их, модель на этих парах отвечает «разные».
    """
    if not heads:
        return []
    key = (keys[i].strip().lower() if keys else "") or None
    sims = vectors[heads] @ vectors[i]
    ranked = sorted(
        ((s, h, key is not None and keys[h].strip().lower() == key) for s, h in zip(sims.tolist(), heads)),
        key=lambda p: (not p[2], -p[0]),
    )
    return [h for s, h, same in ranked if same or s >= BLOCK_COS][:K_SELECT]


def parse(got: dict, n: int) -> int:
    """Номер выбранного варианта; всё, что не номер из `1..n`, — «ни один»."""
    match = got.get("match")
    return match if isinstance(match, int) and 1 <= match <= n else 0


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {"match": {"type": "integer"}},
        "required": ["match"],
        "additionalProperties": False,
    }


async def assign(
    labels: list[str], vectors: np.ndarray, pick: Pick, keys: list[str] | None = None
) -> list[int]:
    """Голова группы для каждого ярлыка (индекс в `labels`; голова указывает на себя).

    Порядок `labels` — порядок старшинства: раньше стоящий становится головой.
    """
    heads: list[int] = []
    out: list[int] = []
    for i, label in enumerate(labels):
        # ⚠️ По одной голове за вызов (см. «Не наступить» в докстринге модуля).
        head = None
        for h in options(vectors, heads, i, keys):
            if await pick(label, [labels[h]]):
                head = h
                break
        if head is None:
            heads.append(i)
            head = i
        out.append(head)
    return out


async def _groups(labels: list[str], vectors: np.ndarray, keys: list[str] | None) -> list[int]:
    client = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
    async with httpx.AsyncClient() as http:

        async def pick(record: str, opts: list[str]) -> int:
            user = f"RECORD: {record}\nOPTIONS:\n" + "\n".join(
                f"{n + 1}. {o}" for n, o in enumerate(opts)
            )
            try:
                got = await client.json_completion(
                    http,
                    system=SYSTEM_PROMPT,
                    user=user,
                    schema=_schema(),
                    name="twins",
                    max_tokens=20,
                )
            except LLMError:
                # Сведение — уборка, а не стадия выдачи: без него запрос отдаёт дубли,
                # с упавшим — не отдаёт ничего.
                return 0
            return parse(got, len(opts))

        return await assign(labels, vectors, pick, keys)


def groups(labels: list[str], vectors: np.ndarray, keys: list[str] | None = None) -> list[int]:
    """Голова группы для каждого ярлыка; `vectors` — нормированные векторы ярлыков,
    `keys` — ядра: общее ядро ставит голову в выбор мимо фильтра по косинусу."""
    return asyncio.run(_groups(labels, vectors, keys)) if labels else []
