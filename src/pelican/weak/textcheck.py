"""Сверка цитаты со страницей: есть ли эта фраза в тексте, который видела модель.

Приём — проверка атрибуции (attribution check, ALCE: Gao et al., EMNLP 2023,
arXiv:2305.14627): вывод модели засчитывается, только если опирается на дословный
кусок источника. Устройство сверки повторяет `inference/verify.py` у wsignal
(github.com/whatanaiceguy/wsignal; лицензии у репозитория нет, поэтому реализация
своя, на `difflib`):

- обе стороны нормализуются: NFKC, кавычки и тире к одному виду, регистр, пробелы;
- цитата режется по многоточиям, фрагменты ищутся ПО ПОРЯДКУ;
- хотя бы один фрагмент не короче `MIN_FRAGMENT` — иначе совпадёт что угодно;
- допускается нечёткое совпадение (модель правит опечатку, склеивает пробелы),
  но числа обязаны совпасть точно: «$49» вместо «$19» — другая цитата.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

MIN_FRAGMENT = 20
# Доля совпадения окна страницы с фрагментом. Как у wsignal по смыслу; число — догадка,
# не замер (docs/todo.md).
SIMILARITY = 0.9

_FOLD = str.maketrans({
    " ": " ", "‘": "'", "’": "'", "“": '"', "”": '"', "«": '"', "»": '"',
    "–": "-", "—": "-", "−": "-", "ё": "е", "Ё": "Е",
})
_WS = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\[\.\.\.\]|\(\.\.\.\)|\.\.\.|…")
_NUMBERS = re.compile(r"\d+(?:[.,]\d+)*")
_QUOTES = "'\" "


def normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_FOLD)
    return _WS.sub(" ", folded).strip().casefold()


def _fragments(quote: str) -> list[str]:
    parts = (normalise(p).strip(_QUOTES) for p in _ELLIPSIS.split(quote))
    return [p for p in parts if p]


def _cuts_number(page: str, start: int, end: int) -> bool:
    before = start > 0 and page[start - 1].isdigit() and page[start:start + 1].isdigit()
    after = end < len(page) and page[end].isdigit() and page[end - 1:end].isdigit()
    return before or after


def _find(fragment: str, page: str, start: int) -> int | None:
    """Конец совпадения фрагмента в `page[start:]` или None."""
    at = page.find(fragment, start)
    while at >= 0:
        if not _cuts_number(page, at, at + len(fragment)):
            return at + len(fragment)
        at = page.find(fragment, at + 1)
    # Нечётко: якорь — самый длинный общий кусок, окно длины фрагмента вокруг него.
    rest = page[start:]
    m = SequenceMatcher(None, rest, fragment, autojunk=False).find_longest_match(
        0, len(rest), 0, len(fragment))
    if m.size == 0:
        return None
    anchor = start + m.a - m.b
    want = _NUMBERS.findall(fragment)
    best: int | None = None
    slack = max(1, len(fragment) // 10)
    for left in range(max(start, anchor - slack), anchor + slack + 1):
        for length in range(len(fragment) - slack, len(fragment) + slack + 1):
            right = left + length
            if right > len(page) or length <= 0:
                continue
            window = page[left:right]
            if (SequenceMatcher(None, window, fragment, autojunk=False).ratio() >= SIMILARITY
                    and _NUMBERS.findall(window) == want
                    and not _cuts_number(page, left, right)):
                best = right if best is None else min(best, right)
    return best


def quote_appears_in(quote: str, page: str) -> bool:
    fragments = _fragments(quote)
    if not fragments or max(len(f) for f in fragments) < MIN_FRAGMENT:
        return False
    text = normalise(page)
    position = 0
    for fragment in fragments:
        end = _find(fragment, text, position)
        if end is None:
            return False
        position = end
    return True
