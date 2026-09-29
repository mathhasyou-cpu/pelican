"""Устойчивость выдачи между прогонами одного запроса: «держится N из K».

Тот же запрос через неделю возвращает ТОП, отличающийся на пять позиций из двенадцати
(docs/weak-measurements.md: три прогона без кэша — пересечение 10 / 5 / 6 из 12), и до
этой пометки в карточке об этом не было ни слова. Здесь прошлые прогоны берутся из
готовых отчётов (таблица `reports`), и у каждого сигнала ТОП
считается, в скольких из них есть то же направление.

- Направления сравниваются `ask.same_direction` — дословно или по значимым словам:
  точное сравнение строк занижает устойчивость.
- ⚠️ Два прогона с дословно одинаковым набором названий — ОДНО наблюдение: с суточным
  кэшем живого поиска ТОП совпадает 12 из 12, и считать такой повтор дважды — накрутка.
  ⚠️ Правило — догадка (docs/todo.md §92).
- ⚠️ `held` в уверенность и в порядок ТОП не входит: это согласие выдачи с самой собой
  (self-consistency), а не качество сигнала — как и условия отбора не входят в балл
  (docs/weak-audit.md).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator
from pathlib import Path

from pelican.weak.ask import AskResult, same_direction

#: Сколько последних прошлых прогонов учитывать. ⚠️ Догадка: меньше — пометка дрожит
#: от одного прогона, больше — считает выдачу кода месячной давности.
HISTORY_RUNS = 5


def _key(query: str) -> str:
    return " ".join(query.split()).casefold()


def _from_dir(reports_dir: Path) -> Iterator[dict]:
    for path in reports_dir.glob("*.json"):
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue


def prior_runs(results: Path | Iterable[dict], query: str, started: str) -> list[list[str]]:
    """Наборы названий ТОП прошлых прогонов ТОГО ЖЕ запроса, от новых к старым.

    `results` — готовые результаты (`reports.result` из базы) или каталог JSON-файлов
    (скрипты замеров). Читаются только `query`, `started` и `signals[].label`, поэтому
    годятся и результаты старых версий. Прогон, начатый не раньше `started`, — не прошлый.
    """
    runs: list[tuple[str, list[str]]] = []
    for data in _from_dir(results) if isinstance(results, Path) else results:
        if not isinstance(data, dict) or _key(str(data.get("query", ""))) != _key(query):
            continue
        when = str(data.get("started", ""))
        if not when or when >= started:
            continue
        labels = [str(s.get("label", "")) for s in data.get("signals", []) if isinstance(s, dict)]
        runs.append((when, [x for x in labels if x]))
    runs.sort(key=lambda r: r[0], reverse=True)
    out: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    for _when, labels in runs:
        fingerprint = frozenset(x.lower() for x in labels)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(labels)
        if len(out) >= HISTORY_RUNS:
            break
    return out


def annotate(result: AskResult, runs: list[list[str]]) -> None:
    """Проставить `Signal.held`, `AskResult.history_runs` и `AskResult.stable`."""
    result.history_runs = len(runs)
    for sig in result.signals:
        sig.held = sum(1 for labels in runs if any(same_direction(sig.label, x) for x in labels))
    floor = math.ceil(len(runs) / 2)
    result.stable = sum(1 for s in result.signals if s.held >= floor) if runs else 0


def overlap(a: list[str], b: list[str]) -> int:
    """Сколько направлений из `a` есть в `b` (тем же сравнением, что и `held`)."""
    return sum(1 for x in a if any(same_direction(x, y) for y in b))
