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
форме. `parse` проверяет буквой по ПРЕДЛОЖЕНИЯМ то, что буквой проверить можно: ссылку
на свидетельство и число, которого нет в процитированных свидетельствах, — такое
предложение вычищается в любом разделе. Хронология `facts` — пункт без даты или без
ссылки отбрасывается.
⚠️ Не возвращать проверку «якоря» (цифра или латинское имя) в «Преимущество» и «Кейс»:
на 30 карточках она сняла 12 предложений, из них 10 — конкретные, с русскими терминами
и аббревиатурами («ХОБЛ», «AMPA»), и оставила пустым «Преимущество» у 12 из 30, а ТЗ
требует его у каждого сигнала (`scripts/measure_card_drops.py`, docs/weak-card.md).

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

from pelican.weak.assess import Evidence, keep_refs
from pelican.weak.textcheck import quote_appears_in

#: Свидетельство в промпте карточки: заголовок + аннотация/сниппет (`weak.deepen`).
#: ⚠️ Догадка (docs/todo.md); у оценки зрелости своя длина — `EVIDENCE_CHARS`.
CARD_EVIDENCE_CHARS = 1100

_REF = re.compile(r"\[([\d,\s]+)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
#: Число с разделителями разрядов: «1,7», «1.7», «570», «12 000».
_NUMBER = re.compile(r"\d[\d.,  ]*\d|\d")
_NOT_DIGIT = re.compile(r"[^\d]")

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
               items it rests on; no numbers that are not in them.
why          — 1 or 2 Russian sentences: why this is a WEAK SIGNAL — still small, but
               already confirmed — and not an established trend or a lone idea. Name what
               is new (the specific technique) and what confirms it independently (another
               group, a patent, a pilot, a company), with citations.
why_quote    — a short fragment, 20 to 160 characters, copied WORD FOR WORD from ONE
               cited item, that best supports "why". Copy, do not paraphrase or translate."""


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
    #: «Почему это слабый сигнал» — по свидетельствам, с ссылками, той же чисткой, что
    #: описание. Пишется карточкой, потому что объяснение оценки зрелости (`assess.parse`)
    #: без ссылки выбрасывается целиком — на стенде так пустело 5–8 карточек из 12–15.
    why: str = ""
    #: Дословный фрагмент свидетельства в опору `why`; пусто — не найден в тексте
    #: (`textcheck.quote_appears_in`, проверка атрибуции ALCE).
    why_quote: str = ""


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "advantage": {"type": "string"},
            "case": {"type": "string"},
            "hypothesis": {"type": "string"},
            "why": {"type": "string"},
            "why_quote": {"type": "string"},
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
            "title", "description", "advantage", "case", "facts", "summaries", "hypothesis",
            "why", "why_quote",
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


def has_cyrillic(text: str) -> bool:
    """Русское ли название: латиница тоже «своё» письмо (`_SCRIPTS`), и английский
    «перевод» иначе проходил бы за русский (NFR-01, PQA-004)."""
    return any(unicodedata.name(ch, "").startswith("CYRILLIC") for ch in text if ch.isalpha())


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


def _clean(text: str, texts: list[str]) -> str:
    """Оставить только предложения со ссылкой на существующее свидетельство и без
    выдуманных чисел. См. докстринг модуля."""
    kept = []
    for s in sentences(text):
        nums = [r for r in refs(s) if 1 <= r <= len(texts)]
        if not nums or foreign_script(s):
            continue
        if invented_numbers(s, [texts[r - 1] for r in nums]):
            continue
        kept.append(keep_refs(s, len(texts)))
    return " ".join(kept)


def parse(payload: dict[str, Any], name: str, evidence: list[Evidence]) -> Card:
    n = len(evidence)
    texts = [evidence_line(ev) for ev in evidence]
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
        text = _clean(str(item.get("text") or ""), texts)
        # Дата пункта — тоже число: она обязана стоять в процитированном свидетельстве.
        cited = [texts[r - 1] for r in refs(text) if 1 <= r <= n]
        if text and digits(day) in digits(" ".join(cited)):
            facts.append((day, text))
    title = " ".join(str(payload.get("title") or "").split())
    if not title or foreign_script(title) or not has_cyrillic(title):
        title = name
    why = _clean(str(payload.get("why") or ""), texts)
    return Card(
        title=title,
        description=_clean(str(payload.get("description") or ""), texts),
        advantage=_clean(str(payload.get("advantage") or ""), texts),
        case=_clean(str(payload.get("case") or ""), texts),
        summaries=summaries,
        facts=sorted(facts),
        hypothesis=_whole(str(payload.get("hypothesis") or ""), texts),
        why=why,
        why_quote=verified_quote(str(payload.get("why_quote") or ""), why, texts),
    )


def verified_quote(quote: str, why: str, texts: list[str]) -> str:
    """Цитата в опору объяснения — только если она дословно стоит в одном из
    свидетельств, на которые объяснение ссылается (`textcheck`, приём ALCE). Нет такого
    свидетельства или фраза не найдена — пусто: цитата не чинится, а не показывается."""
    quote = " ".join(quote.split())
    cited = [texts[r - 1] for r in refs(why) if 1 <= r <= len(texts)]
    if not quote or not cited or foreign_script(quote):
        return ""
    return quote if any(quote_appears_in(quote, t) for t in cited) else ""


def _whole(text: str, texts: list[str]) -> str:
    """Проверка `_clean`, но целиком, без деления на предложения: гипотеза — одно
    предложение, а точка сокращения внутри («… Inc. Bank) начинают…») резала её пополам,
    и половина без ссылки отбрасывалась."""
    text = " ".join(text.split())
    nums = [r for r in refs(text) if 1 <= r <= len(texts)]
    if not nums or foreign_script(text) or invented_numbers(text, [texts[r - 1] for r in nums]):
        return ""
    return keep_refs(text, len(texts))
