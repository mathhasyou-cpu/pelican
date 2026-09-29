"""Тематичность кандидата: о предмете запроса ли его свидетельства.

Отдельный вызов модели, а не строка в промпте оценки (`weak.assess`). Правило «технология
из другой отрасли — шум» там стоит в середине длинного промпта рядом с шестью классами,
стадией и девятью чтениями, и `gemma-4-12b-qat` применяла его через раз: два прогона
«art brut и prison art» дали в ТОП 1 и ~4 кандидата не по теме (стеганография, MCP,
3D-модели). Правило в середине длинного многозадачного промпта соблюдается хуже, чем
то же правило, заданное отдельно.

Приём — **LLM-оценщик релевантности с узким промптом и шкалой 0–3**: оценщик Bing
(Thomas et al., «Large language models can accurately predict searcher preferences», 2023)
и его открытое воспроизведение UMBRELA (Upadhyay et al. 2024, arXiv:2406.06519).
Релевантными там считаются оценки 2 и 3; этот же срез взят здесь (`RELEVANT_MIN`).
Предмет стоит в начале и в конце сообщения: инструкции у краёв контекста модель
соблюдает надёжнее, чем в середине.

⚠️ Судится только то, что иначе попало бы в ТОП: зарождающиеся, по порядку скоринга, до
набора 15 тематичных (`ask._on_topic`). Вызовов на запрос выходит 15–20, а не 45.
⚠️ Без предмета (датасет, `Candidate.area` пуст) проверка не вызывается: замеренный F1
жанра относится к промпту оценки без неё.
"""

from __future__ import annotations

import re
from typing import Any

from pelican.weak.assess import CORPUS, EVIDENCE_CHARS, Evidence

#: Оценки 2 и 3 — «по теме», как у UMBRELA. ⚠️ Срез не замерен на наших запросах
#: (docs/todo.md §100).
RELEVANT_MIN = 2
#: Сколько свидетельств видит проверка: те же, что у оценки, без следа корпуса.
EVIDENCE = 7

_REF = re.compile(r"\[(\d+)\]")

SYSTEM_PROMPT = """You check whether a TECHNOLOGY belongs to the SUBJECT the user asked
about. Judge ONLY by what the EVIDENCE items are about, not by the words of the
technology name and not by what the technology could be used for.

score:
  3  the evidence is about this technology used in, made for, or studying the SUBJECT
     itself.
  2  the evidence shows the technology applied to something the SUBJECT directly
     includes or relies on: its materials, tools, services, infrastructure or security.
  1  the technology could be applied to the SUBJECT, but no evidence item does so.
  0  the evidence is about a different field.

why - ONE sentence in RUSSIAN naming the field the evidence is actually about, citing
  items by number in square brackets, e.g. "Работы [1] и [2] — о криптографии, а не о
  предмете запроса"."""


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "minimum": 0, "maximum": 3},
            "why": {"type": "string"},
        },
        "required": ["score", "why"],
        "additionalProperties": False,
    }


def build_prompt(subject: str, name: str, evidence: list[Evidence]) -> str:
    lines = [f"SUBJECT: {subject}", f"TECHNOLOGY: {name}", "", "EVIDENCE:"]
    shown = [ev for ev in evidence if ev.kind != CORPUS][:EVIDENCE]
    for i, ev in enumerate(shown, 1):
        lines.append(f"[{i}] ({ev.kind}) {ev.text[:EVIDENCE_CHARS]}")
    lines += ["", f"Does this evidence belong to the SUBJECT: {subject}?"]
    return "\n".join(lines)


def parse(payload: dict[str, Any]) -> tuple[int, str]:
    """(оценка 0–3, объяснение). Объяснение без ссылки [N] отбрасывается, как у оценки."""
    score = int(payload.get("score", 0))
    if not 0 <= score <= 3:
        raise ValueError(f"оценка тематичности вне шкалы: {score}")
    why = " ".join(str(payload.get("why") or "").split())
    return score, why if _REF.search(why) else ""
