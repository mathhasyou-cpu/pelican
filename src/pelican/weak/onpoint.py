"""Компания в «Кто делает» — только из заголовка, который ПРО эту технологию.

На стенде у «Adaptive adapter routing» в игроках стояли ASUS, IXOPAY и UGREEN, у «Local
Enhanced Adapters» — Alibaba, Simagic и D-Link: новостной поиск по ярлыку отдал заметки про
роутеры и блоки питания, совпавшие по слову, а извлечение компаний (`money.companies_of`)
честно выписало их имена. Ошибка не в извлечении, а в том, что заголовок не о технологии.

Приём — **проверка релевантности документа узким промптом** (как `weak.topic`: оценщик
релевантности Bing / UMBRELA, Upadhyay et al. 2024, arXiv:2406.06519), здесь бинарная и на
уровне заголовка: один вызов на кандидата, все его заголовки с компаниями разом.

⚠️ Сбой проверки заголовки НЕ снимает: без ответа модели «не про технологию» не доказано
(тот же принцип, что у `ask._on_topic`).
⚠️ Включается `WEAK_ONPOINT_COMPANIES` (`weak.flags`): число игроков входит в скоринг, и
правка меняет порядок ТОП — до замера она выключена.
"""

from __future__ import annotations

from typing import Any

#: Сколько заголовков проверяется за вызов. Больше у кандидата не бывает: показывается
#: `ask.NEWS_PER_CARD` плюс добор `_backing`. ⚠️ Догадка с запасом.
MAX_HEADLINES = 12

SYSTEM_PROMPT = """You check which news HEADLINES are about one TECHNOLOGY.

A headline is about the technology when it reports work on it, a product built on it, a
company developing or deploying it, a result, a round or a pilot for it. A headline that
only shares a word with the technology name is NOT about it: "adapter" in machine learning
is a small trainable module, not a power adapter or a network router; "agent" in AI is
software, not an insurance or estate agent.

Answer "about" — the numbers of the headlines that are about the technology. Return an
empty list if none is."""


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"about": {"type": "array", "items": {"type": "integer", "minimum": 1}}},
        "required": ["about"],
        "additionalProperties": False,
    }


def build_prompt(name: str, area: str, headlines: list[str]) -> str:
    lines = [f"TECHNOLOGY: {name}"]
    if area:
        lines.append(f"FIELD: {area}")
    lines += ["", "HEADLINES:"]
    lines += [f"[{i}] {h}" for i, h in enumerate(headlines[:MAX_HEADLINES], 1)]
    return "\n".join(lines)


def parse(payload: dict[str, Any], n: int) -> set[int]:
    """Номера заголовков «про технологию» (с единицы); чужие номера отбрасываются."""
    got = payload.get("about")
    if not isinstance(got, list):
        raise ValueError(f"нет списка about: {payload!r}")
    return {int(i) for i in got if isinstance(i, int | float) and 1 <= int(i) <= min(n, MAX_HEADLINES)}
