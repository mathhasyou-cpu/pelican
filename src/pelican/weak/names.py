"""Имена компаний как ключ сверки: что считать общим игроком, а что совпадением слов.

Приём — **различительность токена по документной частоте**, тот же, которым в `mine`
отсеиваются неразличительные слова.

⚠️ **Сравниваются различительные слова имени, а не имя целиком**: «Keycard» у нас и
«Keycard, Cyata, Fabrix Security» у методолога — одно и то же, а «Pulse Security» и
«Fabrix Security» — нет, хотя общее слово есть.

⚠️ **Имя, стоящее больше чем в `DF_MAX` строках, не различает ничего**: 89% имён
датасета стоят ровно в одной строке, а «google» — в восьми, «nvidia» — в шести,
«microsoft» — в пяти, и по ним связывается что угодно с чем угодно.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9&.\-]+")
_BRACKETS = re.compile(r"\([^)]*\)")

#: Слова, которые есть в каждом втором названии компании: по ним совпадают не игроки, а
#: отрасли. ⚠️ Слова-области здесь намеренно: «Boston Dynamics» и «Agility Robotics»
#: общим словом «robotics» не связаны никак.
GENERIC = frozenset(
    [
        "ai",
        "ml",
        "security",
        "systems",
        "technologies",
        "technology",
        "labs",
        "lab",
        "inc",
        "ltd",
        "llc",
        "gmbh",
        "corp",
        "group",
        "platform",
        "software",
        "solutions",
        "data",
        "cloud",
        "digital",
        "network",
        "networks",
        "computing",
        "the",
        "and",
        "для",
        "robotics",
        "research",
        "protocol",
        "dynamics",
        "communications",
        "ventures",
        "capital",
    ]
)

#: Во скольких строках датасета имя может стоять, чтобы ещё что-то различать.
DF_MAX = 2


def tokens(text: str) -> set[str]:
    """Различительные слова имён: латиница, без скобок, без общих слов и коротышей."""
    cleaned = _BRACKETS.sub(" ", text or "")
    got = {m.group(0).strip(".").lower() for m in _WORD.finditer(cleaned)}
    return {w for w in got if len(w) > 2 and w not in GENERIC}


def document_frequency(texts: Iterable[str]) -> Counter[str]:
    """Сколько РАЗНЫХ строк упоминают каждое имя."""
    df: Counter[str] = Counter()
    for text in texts:
        for token in tokens(text):
            df[token] += 1
    return df


def discriminating(text: str, df: Counter[str]) -> set[str]:
    """Слова имени, которые ещё что-то различают на этом наборе строк."""
    return {t for t in tokens(text) if df.get(t, 0) <= DF_MAX}
