"""Устойчивость между прогонами: история читается из JSON, одинаковые ТОП — одно наблюдение."""

from __future__ import annotations

import json
from pathlib import Path

from pelican.weak import history
from pelican.weak.ask import AskResult, Signal
from pelican.weak.kinds import EMERGING
from pelican.weak.rubric import PILOT


def _dump(folder: Path, name: str, query: str, started: str, labels: list[str]) -> None:
    data = {"query": query, "started": started, "signals": [{"label": x} for x in labels]}
    (folder / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _sig(label: str) -> Signal:
    return Signal(label, label, EMERGING, "", PILOT, 1.0, [], "")


def test_prior_runs_same_query_only_earlier_and_deduped(tmp_path: Path) -> None:
    q = "слабые сигналы в кибербезопасности"
    _dump(tmp_path, "a", q, "2026-09-10T10:00:00+00:00", ["agent identity", "mcp scanners"])
    _dump(tmp_path, "b", q, "2026-09-11T10:00:00+00:00", ["mcp scanners", "agent identity"])
    _dump(
        tmp_path, "c", "  Слабые сигналы в кибербезопасности ", "2026-09-12T10:00:00+00:00", ["x"]
    )
    _dump(tmp_path, "d", "перспективные решения в финтехе", "2026-09-12T10:00:00+00:00", ["y"])
    _dump(tmp_path, "e", q, "2026-09-20T10:00:00+00:00", ["future"])
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    got = history.prior_runs(tmp_path, q, "2026-09-15T00:00:00+00:00")
    # Чужой запрос и прогон из будущего не берутся; b дублирует a дословно — одно наблюдение;
    # порядок — от новых к старым; регистр и пробелы запроса не важны.
    assert got == [["x"], ["mcp scanners", "agent identity"]]


def test_prior_runs_capped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(history, "HISTORY_RUNS", 2)
    for i in range(4):
        _dump(tmp_path, f"r{i}", "q", f"2026-09-0{i + 1}T00:00:00", [f"l{i}"])
    assert history.prior_runs(tmp_path, "q", "2026-09-09T00:00:00") == [["l3"], ["l2"]]


def test_annotate_counts_held_by_direction_and_stable_by_half() -> None:
    result = AskResult("q", "q", [], "2026-09-15T00:00:00")
    result.signals = [_sig("security and governance for agentic ai"), _sig("new thing")]
    runs = [
        ["security infrastructure for agentic ai"],
        ["security governance for agentic ai", "other"],
        ["unrelated"],
    ]
    history.annotate(result, runs)
    assert result.history_runs == 3
    assert [s.held for s in result.signals] == [2, 0]
    assert result.stable == 1  # держится в ≥ ceil(3/2) = 2 прогонах
    history.annotate(result, [])
    assert (result.history_runs, result.stable) == (0, 0)
    assert [s.held for s in result.signals] == [0, 0]


def test_overlap_uses_same_comparison() -> None:
    assert history.overlap(["agent identity", "x"], ["Agent identity", "y"]) == 1
