"""Русская карточка слабого сигнала: текст пишет модель, но только по свидетельствам.

Разделы — дословно из ТЗ: «описание технологии, потенциальное преимущество,
кейс-пример, источник или источники и объяснение отнесения технологии к
зарождающемуся тренду».

⚠️ **Текст опирается только на переданные свидетельства.** ТЗ запрещает выдачу «на
основании знаний языковой модели без подтверждённого поиска», поэтому каждый раздел
обязан ссылаться на номера свидетельств, и `parse` вычищает раздел без единой ссылки:
пустое поле честнее, чем правдоподобная выдумка.

⚠️ **Факт, а не общее место.** Карточку читает специалист, и «технология обладает
потенциалом для трансформации» для него — потерянное время. Промпт требует в каждом
предложении проверяемый факт (дату, число, названную организацию или работу) — приём
entity-dense summary, Chain of Density (Adams и др. 2023, arXiv:2309.15217) в одношаговой
форме, — а `parse` проверяет это буквой по ПРЕДЛОЖЕНИЯМ: в «Преимуществе» и «Кейсе»
предложение без якоря вычищается, а число, которого нет в процитированных
свидетельствах, вычищает предложение в любом разделе. Хронология `facts` — пункт без
даты или без ссылки отбрасывается.

⚠️ **Резюме иностранного источника помечается как машинное.** ТЗ: «при использовании
автоматического перевода или генеративного резюме это должно быть отмечено возле
источника». Поле `summaries` — русская строка на КАЖДЫЙ процитированный источник, и
страница рисует рядом с ней отметку, если язык оригинала не русский.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from pelican.weak.assess import Evidence

#: Свидетельство в промпте карточки: заголовок + аннотация/сниппет (`weak.deepen`).
#: ⚠️ Догадка (docs/todo.md); у оценки зрелости своя длина — `EVIDENCE_CHARS`.
CARD_EVIDENCE_CHARS = 1100

_REF = re.compile(r"\[([\d,\s]+)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
#: Число с разделителями разрядов: «1,7», «1.7», «570», «12 000».
_NUMBER = re.compile(r"\d[\d.,  ]*\d|\d")
_NOT_DIGIT = re.compile(r"[^\d]")
#: Латинское имя собственное: компания, продукт, модель, издатель.
_NAME = re.compile(r"\b[A-Z][A-Za-z0-9]*[a-z0-9][A-Za-z0-9-]*\b|\b[A-Z]{2,}[0-9]+\b")
#: Термины, которые пишутся с заглавной, но фактом не являются.
_GENERIC = frozenset(
    "AI LLM LLMs ML MoE UI API GPU CPU NPU Transformer Transformers Attention Agent Agents "
    "Generative Quantum Machine Learning Large Language Model Models World Deep Neural".split()
)

SYSTEM_PROMPT = """You write an analyst card in RUSSIAN about one emerging TECHNOLOGY,
using ONLY the numbered EVIDENCE. The reader is a specialist: a sentence that is true
but carries no fact ("the technology has great potential", "improves efficiency
compared to traditional methods", "attracts investor interest") wastes their time and
is FORBIDDEN. Every sentence must carry at least one concrete fact taken from the
evidence: a date (month and year), a number (amount raised, accuracy, speed-up, size,
count) or a named company, product, model, lab or paper — and cite it by number in
square brackets. Copy numbers exactly as the evidence states them. Never add facts,
companies, numbers or dates that are not in the evidence. If the evidence has no fact
for a field, return an empty string for it rather than a general phrase.

title        — the technology name in Russian, 3 to 10 words; keep established English
               terms (LLM, MCP, TEE) as they are.
description  — 2 or 3 sentences: what the technology is and how it works, with the
               specific method, architecture or result named in the evidence.
advantage    — 1 or 2 sentences: the measured or claimed advantage, with its number
               and who reported it.
case         — 1 or 2 sentences: who built, tested, piloted or funded it, when, and
               what happened (amount, result).
facts        — 3 to 6 dated facts about THIS technology, oldest first: date as YYYY-MM
               (or YYYY) and one short Russian sentence "who did what" with the
               citation. Skip evidence that is about something else, and prefer
               releases, results, rounds and pilots over "a paper was published".
summaries    — for EACH evidence item you cited: its number and one Russian sentence
               saying what that item reports.
hypothesis   — ONE Russian sentence for the analyst to verify next: "Если <condition
               that the evidence shows is starting>, то <what to expect within 1-2
               years and for whom>". Build it only on facts from the evidence, cite the
               items it rests on; no numbers that are not in them."""


@dataclass(slots=True)
class Card:
    title: str
    description: str
    advantage: str
    case: str
    summaries: dict[int, str] = field(default_factory=dict)
    #: Хронология: (дата YYYY-MM, «кто что сделал [n]»).
    facts: list[tuple[str, str]] = field(default_factory=list)
    #: «Сигнал найден → генерация гипотезы» (схема 1 ТЗ): проверяемое «если…, то…» для
    #: эксперта. Проходит ту же чистку, что описание: без ссылки или с выдуманным числом — пусто.
    hypothesis: str = ""


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "advantage": {"type": "string"},
            "case": {"type": "string"},
            "hypothesis": {"type": "string"},
            "facts": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {"date": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["date", "text"],
                    "additionalProperties": False,
                },
            },
            "summaries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"n": {"type": "integer"}, "text": {"type": "string"}},
                    "required": ["n", "text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "title", "description", "advantage", "case", "facts", "summaries", "hypothesis"
        ],
        "additionalProperties": False,
    }


def evidence_line(ev: Evidence) -> str:
    """Текст свидетельства, который видит модель карточки, — и против которого `parse`
    сверяет числа. Одна функция на оба конца, иначе сверка разойдётся с промптом."""
    text = f"{ev.text} — {ev.detail}" if ev.detail else ev.text
    who = f", {ev.publisher}" if ev.publisher else ""
    return f"({ev.date}, {ev.kind}{who}) {text[:CARD_EVIDENCE_CHARS]}"


def build_prompt(name: str, evidence: list[Evidence]) -> str:
    lines = [f"TECHNOLOGY: {name}", "", "EVIDENCE:"]
    for i, ev in enumerate(evidence, 1):
        lines.append(f"[{i}] {evidence_line(ev)}")
    return "\n".join(lines)


def digits(text: str) -> str:
    """Цифры подряд, без разделителей: «1 700» и «1,700» — одно и то же число."""
    return _NOT_DIGIT.sub("", text)


def refs(text: str) -> list[int]:
    """Номера ссылок; «[1, 3]» — две ссылки."""
    return [int(r) for group in _REF.findall(text) for r in group.replace(" ", "").split(",") if r]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(" ".join((text or "").split())) if s.strip()]


#: Письма, которыми карточка вправе писать: русский текст, английские термины и греческие
#: буквы формул (μ, α). Всё прочее — сбой генерации: gemma вставляла деванагари посреди
#: русского слова («Смартфон-आधारные»), а ТЗ требует выдачу на русском.
_SCRIPTS = ("LATIN", "CYRILLIC", "GREEK")


def foreign_script(text: str) -> bool:
    """Есть ли в тексте буква чужого письма — проверка вывода по Unicode-скрипту
    (output guardrail): такой текст не показывается, а не чинится."""
    return any(
        ch.isalpha() and not unicodedata.name(ch, "").startswith(_SCRIPTS) for ch in text
    )


def drop_foreign(text: str) -> str:
    """Текст без предложений с чужим письмом (`foreign_script`) — для полей, которые не
    проходят `_clean`: объяснения оценки и тематичности, поля отчётов, собранных до проверки."""
    if not text or not foreign_script(text):
        return text
    return " ".join(s for s in sentences(text) if not foreign_script(s))


def invented_numbers(sentence: str, cited: list[str]) -> list[str]:
    """Числа предложения, которых нет в процитированных свидетельствах.

    ⚠️ Сверяется ЧИСЛО, а не единица: «$66M» и «66 миллионов» — одно. Однозначное
    («2 компании») не сверяется. Цифры свидетельств склеены в одну строку, поэтому
    ошибка возможна только в снисходительную сторону (`scripts/measure_card_facts.py`).
    """
    pool = digits(" ".join(cited))
    out = []
    for raw in _NUMBER.findall(_REF.sub(" ", sentence)):
        num = digits(raw)
        if len(num) >= 2 and num not in pool:
            out.append(raw.strip())
    return out


def has_anchor(sentence: str, name: str, companies: list[str]) -> bool:
    """Есть ли в предложении проверяемый якорь: число, дата или имя собственное.

    Латинское слово с заглавной — имя, если это не общий термин и не слово самого
    названия технологии («World Models» в карточке о world models — не факт).
    """
    body = _REF.sub(" ", sentence)
    if re.search(r"\d", body) or any(c and c in body for c in companies):
        return True
    own = {w.lower() for w in re.findall(r"[A-Za-z]+", name)}
    return any(w not in _GENERIC and w.lower() not in own for w in _NAME.findall(body))


def _clean(
    text: str, texts: list[str], name: str, companies: list[str], anchored: bool
) -> str:
    """Оставить только предложения со ссылкой на существующее свидетельство, без
    выдуманных чисел и — где `anchored` — с якорем. См. докстринг модуля."""
    kept = []
    for s in sentences(text):
        nums = [r for r in refs(s) if 1 <= r <= len(texts)]
        if not nums or foreign_script(s):
            continue
        if invented_numbers(s, [texts[r - 1] for r in nums]):
            continue
        if anchored and not has_anchor(s, name, companies):
            continue
        kept.append(s)
    return " ".join(kept)


def parse(
    payload: dict[str, Any], name: str, evidence: list[Evidence], companies: list[str] = ()
) -> Card:
    n = len(evidence)
    texts = [evidence_line(ev) for ev in evidence]
    companies = list(companies)
    summaries = {}
    for item in payload.get("summaries") or []:
        k = int(item.get("n") or 0)
        text = " ".join(str(item.get("text") or "").split())
        if 1 <= k <= n and text and not foreign_script(text):
            summaries[k] = text
    facts = []
    for item in payload.get("facts") or []:
        # Модель пишет и «2026-03», и «2026-03-10»; в хронологии — до месяца.
        day = str(item.get("date") or "").strip()
        if not re.fullmatch(r"(19|20)\d\d(-[01]\d(-[0-3]\d)?)?", day):
            continue
        day = day[:7]
        text = _clean(str(item.get("text") or ""), texts, name, companies, False)
        # Дата пункта — тоже число: она обязана стоять в процитированном свидетельстве.
        cited = [texts[r - 1] for r in refs(text) if 1 <= r <= n]
        if text and digits(day) in digits(" ".join(cited)):
            facts.append((day, text))
    title = " ".join(str(payload.get("title") or "").split())
    if not title or foreign_script(title):
        title = name
    return Card(
        title=title,
        description=_clean(str(payload.get("description") or ""), texts, name, companies, False),
        advantage=_clean(str(payload.get("advantage") or ""), texts, name, companies, True),
        case=_clean(str(payload.get("case") or ""), texts, name, companies, True),
        summaries=summaries,
        facts=sorted(facts),
        hypothesis=_whole(str(payload.get("hypothesis") or ""), texts),
    )


def _whole(text: str, texts: list[str]) -> str:
    """Проверка `_clean`, но целиком, без деления на предложения: гипотеза — одно
    предложение, а точка сокращения внутри («… Inc. Bank) начинают…») резала её пополам,
    и половина без ссылки отбрасывалась."""
    text = " ".join(text.split())
    nums = [r for r in refs(text) if 1 <= r <= len(texts)]
    if not nums or foreign_script(text) or invented_numbers(text, [texts[r - 1] for r in nums]):
        return ""
    return text
