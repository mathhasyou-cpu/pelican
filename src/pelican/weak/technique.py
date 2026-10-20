"""Приём за направлением: какое конкретное ноу-хау отличает его от своей категории.

Эксперты заказчика (консультация 16.10) про финтех-ТОП: «90% перечисленного — достаточно
зрелые технологии, хочется более точечные». В ТОП попадали категории — «DeFi», «Embedded
Finance», «RAG», «Zero Trust», а строки датасета устроены иначе: технология плюс новый угол
(«федеративное обучение для межбанковского AML», «red-teaming как услуга»).

Приём перенесён из copycat (`copycat/labels.py`, `KEY_CAP_SYSTEM`): модель называет
КАТЕГОРИЮ («что делают обычные представители») и ОДНУ работу сверх неё. Там это вскрыло
главную ошибку судьи — «та же категория» читалась как «то же самое» (~45% ложных). Здесь
вопрос тот же, но по свидетельствам: какой конкретный метод, архитектура или ноу-хау
названы в работах и новостях. Нет такого — направление и есть категория, и уходит в
«слишком общая категория» с причиной.

⚠️ Приём обязан стоять в свидетельстве (`cite`): иначе модель называет приём по памяти,
а ТЗ запрещает выдачу на знаниях модели без подтверждённого поиска.
⚠️ Включается `WEAK_TECHNIQUE` (`weak.flags`): снимает кандидатов из ТОП — до замера выключен.
"""

from __future__ import annotations

from typing import Any

from pelican.weak.assess import CORPUS, EVIDENCE_CHARS, Evidence

#: Свидетельств в промпте: те же, что у тематичности, без следа корпуса.
EVIDENCE = 7

SYSTEM_PROMPT = """You read the EVIDENCE about one TECHNOLOGY direction.

category  — what this direction is IN GENERAL, a short phrase ("retrieval-augmented
            generation", "decentralized finance", "humanoid robots").
technique — the ONE specific method, architecture, material or know-how that the
            evidence names and that ordinary work in that category does NOT have, a
            short phrase ("graph-indexed retrieval over knowledge bases", "on-chain FX
            between non-USD stablecoins"). Name a method, not a quality: never "better",
            "faster", "secure", "AI-powered", "scalable" as such.
specific  — true only if the evidence really names such a technique; false if the
            direction is just its category under another name.
cite      — the number of the evidence item that names the technique; 0 if none."""


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "technique": {"type": "string"},
            "specific": {"type": "boolean"},
            "cite": {"type": "integer", "minimum": 0},
        },
        "required": ["category", "technique", "specific", "cite"],
        "additionalProperties": False,
    }


def _shown(evidence: list[Evidence]) -> list[Evidence]:
    return [ev for ev in evidence if ev.kind != CORPUS][:EVIDENCE]


def build_prompt(name: str, evidence: list[Evidence]) -> str:
    lines = [f"TECHNOLOGY: {name}", "", "EVIDENCE:"]
    for i, ev in enumerate(_shown(evidence), 1):
        lines.append(f"[{i}] ({ev.kind}) {ev.text[:EVIDENCE_CHARS]}")
    return "\n".join(lines)


def parse(payload: dict[str, Any], evidence: list[Evidence]) -> tuple[str, str]:
    """(категория, приём). Приём пуст, если модель его не нашла или не процитировала
    существующее свидетельство — такой ответ неотличим от знаний модели."""
    category = " ".join(str(payload.get("category") or "").split())
    technique = " ".join(str(payload.get("technique") or "").split())
    cite = int(payload.get("cite") or 0)
    if not payload.get("specific") or not technique or not 1 <= cite <= len(_shown(evidence)):
        technique = ""
    return category, technique
