"""Контракт структурных событий прогона: этапы и обязательные поля каждого вида.

Конвейер (`weak.ask.run(on_event=...)`) шлёт события, исполнитель API пишет их в
`job_events`, экран ожидания веб-интерфейса проигрывает их (`web/src/screens/Wait.jsx`).
Описание — pelican/docs/techdoc.md, «Ход прогона».

⚠️ Этапы — одна таблица на все стороны: конвейер и API берут их отсюда, а фронт держит
копию в `web/src/lib/timeline.js` (ей нужны подписи до первого события). Совпадение
копии проверяет `tests/test_events.py`; расхождение id ломает полосу ожидания молча.
Модуль лёгкий: его импортирует `pelican.api`, которому тяжёлый конвейер не нужен.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("pelican.events")

#: Этапы прогона по порядку: (id, подпись). Последний, `save`, шлёт исполнитель API.
RUN_STAGES = (
    ("expand", "раскрываю запрос в направления"),
    ("science", "ищу по локальному корпусу и называю технологию в работах"),
    ("money", "денежный поток: читаю заголовки о раундах, пилотах, сделках"),
    ("merge", "свожу кандидатов к ядру, сверяю близнецов"),
    ("evidence", "меряю след в корпусе, живой поиск, нахожу компании"),
    ("assess", "оцениваю зрелость кандидатов"),
    ("score", "скоринг моделью роста, проверяю соответствие запросу"),
    ("patents", "патентная активность финалистов"),
    ("cards", "углубление и карточки"),
    ("save", "сохраняю отчёт"),
)
SAVE = "save"
_NAMES = dict(RUN_STAGES)
_ORDER = {sid: n for n, (sid, _name) in enumerate(RUN_STAGES, 1)}

#: Поля, которые читает экран ожидания, по виду события.
REQUIRED: dict[str, frozenset[str]] = {
    "stage": frozenset({"id", "name", "n", "of"}),
    "facets": frozenset({"facets_ru", "submarkets"}),
    "verdict": frozenset({"label", "title", "kind", "kind_label", "stage", "why", "facet", "sources"}),
    "relevance": frozenset({"label", "relevance"}),
    "top": frozenset({"labels", "confidence"}),
    "patents": frozenset({"patents"}),
    "card": frozenset({"label", "title"}),
    "titles": frozenset({"titles"}),
    "done": frozenset({"report"}),
    "failed": frozenset({"error", "error_detail"}),
}
#: `counts` приходит тремя порциями по ходу прогона — каждая со своим набором чисел.
COUNTS = (
    frozenset({"works", "works_named"}),
    frozenset({"headlines", "directions"}),
    frozenset({"candidates", "categories_removed"}),
)

Emit = Callable[[str, dict[str, Any]], None]


def stage(sid: str) -> dict[str, Any]:
    """Данные события `stage` для этапа `sid`."""
    return {"id": sid, "name": _NAMES[sid], "n": _ORDER[sid], "of": len(RUN_STAGES)}


def problems(kind: str, data: dict[str, Any]) -> list[str]:
    """Чего не хватает событию по контракту; пусто — событие годно."""
    if kind == "counts":
        return [] if any(g <= data.keys() for g in COUNTS) else [f"counts: нет ни одной порции {sorted(map(sorted, COUNTS))}"]
    if kind not in REQUIRED:
        return [f"неизвестный вид события {kind!r}"]
    missing = REQUIRED[kind] - data.keys()
    return [f"{kind}: нет полей {sorted(missing)}"] if missing else []


def checked(emit: Emit) -> Emit:
    """Хук, который сверяет событие с контрактом перед отправкой. Нарушение — в лог, а
    событие всё равно уходит: витрина не должна ронять двадцать минут счёта."""

    def send(kind: str, data: dict[str, Any]) -> None:
        for p in problems(kind, data):
            log.warning("событие вне контракта: %s", p)
        emit(kind, data)

    return send
