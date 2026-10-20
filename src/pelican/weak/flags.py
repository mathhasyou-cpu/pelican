"""Переключатели доработок после консультации 16.10 — каждая замеряется отдельно.

Всё, что меняет СОСТАВ или ПОРЯДОК ТОП, выключено по умолчанию и включается переменной
окружения только после замера с правилом, назначенным до прогона (CLAUDE.md: «пороги
подбираются замером»). Порядок замеров и правила — `pelican/docs/final-measure.md`.

| переменная | что включает | модуль |
|---|---|---|
| `WEAK_ONPOINT_COMPANIES` | компания идёт в «Кто делает», только если её заголовок — про эту технологию | `weak.onpoint` |
| `WEAK_LAYERS` | три слоя выдачи: уже тренд / слабый сигнал / зарождающееся | `weak.layers` |
| `WEAK_TECHNIQUE` | приём (ноу-хау) за направлением; нет приёма — «слишком общая категория» | `weak.technique` |
| `WEAK_EXTRA_SOURCES` | гранты, препринты, клинические испытания, стартапы в живом поиске | `weak.extra` |
| `WEAK_BURST` | направления поиска из всплеска в корпусе: `1` — к направлениям модели, `only` — вместо них | `weak.burst` |
| `WEAK_LISTED_IN_ASK` | модель списка заказчика первым ключом порядка ТОП | `weak.ask` (`LISTED_IN_ASK`) |
| `WEAK_DATED_FACETS` | под срезом (`weak.asof`): дата среза в промпте направлений, направление без работ в корпусе на дату — вон, место добирает всплеск | `weak.ask.expand` |
"""

from __future__ import annotations

import os


def _on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


ONPOINT_COMPANIES = _on("WEAK_ONPOINT_COMPANIES")
LAYERS = _on("WEAK_LAYERS")
TECHNIQUE = _on("WEAK_TECHNIQUE")
LISTED_IN_ASK = _on("WEAK_LISTED_IN_ASK")
EXTRA_SOURCES = _on("WEAK_EXTRA_SOURCES")
#: Направления на дату среза (lookahead bias, Sarkar & Vafa 2024): без него gemma на любом
#: срезе называет темы своих 2024–2025, и ретро ищет в корпусе 2017 «long-context LLMs».
DATED_FACETS = _on("WEAK_DATED_FACETS")
#: "" — выключено, "add" — к направлениям модели, "only" — вместо них (честный ретро-тест:
#: направления модели знают будущее, docs/todo.md §112).
BURST = (
    "only"
    if os.getenv("WEAK_BURST", "").strip().lower() == "only"
    else ("add" if _on("WEAK_BURST") else "")
)
