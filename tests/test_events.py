"""Контракт событий прогона (`pelican.events`): этапы совпадают с фронтом, настоящий
`ask.run` шлёт события по контракту и этапы — по порядку."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace

from pelican import events
from pelican.weak import ask as ask_mod
from pelican.weak.kinds import EMERGING
from pelican.weak.rubric import PILOT

TIMELINE = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "timeline.js"


def test_frontend_timeline_has_the_same_stages():
    """Копия этапов во фронте (`timeline.js`) — те же id и подписи, в том же порядке."""
    text = TIMELINE.read_text(encoding="utf-8")
    front = re.findall(r'\{ id: "(\w+)",\s*at: \d+,\s*name: "([^"]+)" \}', text)
    assert front == list(events.RUN_STAGES)


def test_stage_event_and_contract_check():
    assert events.stage("money") == {"id": "money", "name": events.RUN_STAGES[2][1], "n": 3, "of": 10}
    assert events.problems("top", {"labels": []}) == ["top: нет полей ['confidence']"]
    assert events.problems("counts", {"works": 1, "works_named": 0}) == []
    assert events.problems("counts", {"works": 1})
    assert events.problems("bogus", {})


def test_checked_logs_but_still_sends(caplog):
    sent = []
    with caplog.at_level(logging.WARNING, logger="pelican.events"):
        events.checked(lambda k, d: sent.append(k))("top", {})
    assert sent == ["top"] and "вне контракта" in caplog.text


def _stub_pipeline(monkeypatch):
    """Настоящий `ask.run` без модели, корпуса и сети: один денежный кандидат доходит до ТОП."""
    m = ask_mod
    noop = lambda *a, **k: None  # noqa: E731
    monkeypatch.setattr(m, "expand", lambda q: m.Expansion(["платежи"], "agent payments", "fintech", ["payments"], ["cards"]))
    monkeypatch.setattr(m, "ground", lambda store, names, k: [[] for _ in names])
    stats = SimpleNamespace(pending=0, failed_batches=0, echo_swap=0, unreachable=0)
    monkeypatch.setattr(m, "run_tech", lambda store, ids: ({}, stats))
    monkeypatch.setattr(m, "_mentions", lambda store, ids: {})
    monkeypatch.setattr(m, "_papers", lambda store, ids: {})
    monkeypatch.setattr(m, "money_queries", lambda *a: [])
    money = SimpleNamespace(label="agent mandates", companies=["Visa", "Stripe"], headlines=[])
    monkeypatch.setattr(m, "money_candidates", lambda qs, say, area: ([money], 40, SimpleNamespace(note=lambda: "")))
    monkeypatch.setattr(m, "cores", lambda labels: list(labels))
    monkeypatch.setattr(m, "_merge_near", lambda cands, words: cands)
    monkeypatch.setattr(m, "_merge_twins", lambda cands: cands)
    monkeypatch.setattr(m, "is_category", lambda label, words: False)
    monkeypatch.setattr(m, "footprints", lambda store, names, loose=False: {})
    monkeypatch.setattr(m, "assign_facets", lambda cands, facets, hits, by_tech: {c.label: 0 for c in cands})
    monkeypatch.setattr(m, "news_many_measured", lambda labels, lang: [[] for _ in labels])
    monkeypatch.setattr(m, "live_search", lambda q, area: SimpleNamespace(queried={}, errors={}, docs=[]))
    monkeypatch.setattr(m, "companies_of", lambda docs: [])
    monkeypatch.setattr(m, "_assess", lambda c: SimpleNamespace(kind=EMERGING, stage=PILOT, why="Раунд [1].", cited=[1], judgments={}))
    monkeypatch.setattr(m, "score", lambda pool, say: False)

    def on_topic(shown, area, result, n):
        for s, _ in shown:
            s.relevance = 3
        return shown

    monkeypatch.setattr(m, "_on_topic", on_topic)
    for name in ("attach_patents", "deepen_top", "write_cards", "russian_summaries", "russian_titles"):
        monkeypatch.setattr(m, name, noop)
    monkeypatch.setattr(m, "answer_models", lambda: [])


def test_real_run_emits_contract_events_in_stage_order(monkeypatch):
    _stub_pipeline(monkeypatch)
    got: list[tuple[str, dict]] = []
    result = ask_mod.run(None, "платежи агентов", on_event=lambda k, d: got.append((k, d)))

    for kind, data in got:
        assert events.problems(kind, data) == [], (kind, data)
    stages = [d["id"] for k, d in got if k == "stage"]
    # всё, кроме `save` (его шлёт исполнитель API), по порядку контракта
    assert stages == [sid for sid, _ in events.RUN_STAGES if sid != events.SAVE]
    kinds = [k for k, _ in got]
    for kind in ("facets", "counts", "verdict", "relevance", "top", "patents", "card", "titles"):
        assert kind in kinds, kind
    top = next(d for k, d in got if k == "top")
    assert top["labels"] == [s.label for s in result.signals] == ["agent mandates"]
    verdict = next(d for k, d in got if k == "verdict")
    assert verdict["facet"] == 0 and verdict["stage"] == PILOT


def test_layers_run_announces_patents_stage(monkeypatch):
    """Со слоями патенты считаются внутри отбора — стадия 8 всё равно объявляется (PQA-016)."""
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(ask_mod.flags, "LAYERS", True)
    monkeypatch.setattr(ask_mod, "_drop_trends", lambda shown, result: shown)
    monkeypatch.setattr(ask_mod, "_split_layers", lambda shown, result, say, **_k: shown)
    got: list[tuple[str, dict]] = []
    ask_mod.run(None, "платежи агентов", on_event=lambda k, d: got.append((k, d)))

    stages = [d["id"] for k, d in got if k == "stage"]
    assert stages == [sid for sid, _ in events.RUN_STAGES if sid != events.SAVE]
    assert "patents" in [k for k, _ in got]
