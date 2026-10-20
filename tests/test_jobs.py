"""Очередь запросов и отчёты: живой Postgres, отдельная тестовая база."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from pelican import jobs


@pytest.fixture
def conn(pg_url):
    c = jobs.connect(pg_url)
    jobs.apply_schema(c)
    c.execute("TRUNCATE jobs, reports, job_events RESTART IDENTITY")
    yield c
    c.close()


def test_history_reads_only_labels_of_the_same_query(conn):
    """История для подписи устойчивости — метки того же запроса, без полных отчётов."""
    def save(name, query, started, labels):
        result = {"query": query, "started": started, "signals": [{"label": x, "sources": ["…"]} for x in labels]}
        jobs.save_report(conn, name, query, datetime.fromisoformat(started), json.dumps(result), "")

    save("a", "Технологии в ИИ", "2026-10-01T10:00:00+00:00", ["x", "y"])
    save("b", "технологии  в ии", "2026-10-02T10:00:00+00:00", ["z"])
    save("c", "финтех", "2026-10-03T10:00:00+00:00", ["w"])
    got = jobs.history(conn, "технологии в ИИ")
    assert [r["started"][:10] for r in got] == ["2026-10-02", "2026-10-01"]
    assert got[1]["signals"] == [{"label": "x"}, {"label": "y"}]


def test_fifo_positions_and_autostart_order(conn):
    a, b, c = (jobs.enqueue(conn, q) for q in ("первый", "второй", "третий"))
    assert [jobs.snapshot(conn, i)["position"] for i in (a, b, c)] == [0, 1, 2]
    assert jobs.snapshot(conn, b)["running_ahead"] is False

    got = jobs.claim(conn)
    assert got.id == a
    # Идущий считается: второй — «один впереди», третий — «два впереди».
    assert jobs.snapshot(conn, a)["status"] == "running"
    assert [jobs.snapshot(conn, i)["position"] for i in (b, c)] == [1, 2]
    assert jobs.snapshot(conn, b)["running_ahead"] is True

    jobs.finish(conn, a, "r1")
    assert jobs.claim(conn).id == b
    jobs.fail(conn, b, "boom")
    assert jobs.claim(conn).id == c
    assert jobs.claim(conn) is None


def test_eta_grows_with_queue(conn):
    a, b, c = (jobs.enqueue(conn, q) for q in ("x", "y", "z"))
    jobs.claim(conn)
    eb, ec = jobs.snapshot(conn, b)["eta_start_s"], jobs.snapshot(conn, c)["eta_start_s"]
    assert 0 < eb < ec
    assert ec - eb == pytest.approx(jobs.DEFAULT_RUN_S, abs=1)


def test_requeue_stale_running(conn):
    a = jobs.enqueue(conn, "брошенный")
    jobs.claim(conn)
    jobs.say(conn, a, "10 с · стадия")
    assert jobs.requeue_stale(conn) == 1
    snap = jobs.snapshot(conn, a)
    assert snap["status"] == "queued" and snap["progress"] == []
    assert jobs.claim(conn).id == a


def test_progress_appends(conn):
    a = jobs.enqueue(conn, "q")
    jobs.say(conn, a, "1 с · раз")
    jobs.say(conn, a, "2 с · два")
    assert jobs.snapshot(conn, a)["progress"] == ["1 с · раз", "2 с · два"]


def test_active_limit_per_client(conn):
    ids = [jobs.enqueue(conn, q, "10.0.0.1") for q in ("a", "b", "c")]
    with pytest.raises(jobs.Busy):
        jobs.enqueue(conn, "d", "10.0.0.1")
    jobs.enqueue(conn, "без адреса")  # пустой client не ограничен
    jobs.enqueue(conn, "без адреса")

    jobs.claim(conn)
    jobs.fail(conn, ids[0], "boom")
    jobs.enqueue(conn, "d", "10.0.0.1")  # освободилось место


def test_active_limit_holds_under_concurrency(conn, pg_url):
    def attempt(i):
        with jobs.connect(pg_url) as c:
            try:
                return jobs.enqueue(c, f"гонка {i}", "10.0.0.2")
            except jobs.Busy:
                return None

    with ThreadPoolExecutor(8) as pool:
        got = [r for r in pool.map(attempt, range(8)) if r is not None]
    assert len(got) == jobs.MAX_ACTIVE_PER_CLIENT


def test_same_query_at_once_is_one_job(conn, pg_url):
    """Восемь одинаковых запросов в одну секунду — одно задание, а не восемь прогонов."""
    def attempt(_):
        with jobs.connect(pg_url) as c:
            return jobs.enqueue(c, "Один и тот же")

    with ThreadPoolExecutor(8) as pool:
        got = set(pool.map(attempt, range(8)))
    assert len(got) == 1
    assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1


def test_running_snapshot_has_typical_duration(conn):
    a = jobs.enqueue(conn, "q")
    jobs.claim(conn)
    assert jobs.snapshot(conn, a)["typical_run_s"] == jobs.DEFAULT_RUN_S


def test_report_roundtrip(conn):
    started = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    payload = json.dumps({"query": "q", "signals": [{"label": "x"}]}, ensure_ascii=False)
    jobs.save_report(conn, "20260924-120000", "q", started, payload, "<html></html>")
    got = jobs.report(conn, "20260924-120000")
    assert got["result"]["signals"][0]["label"] == "x"
    assert [r["name"] for r in jobs.reports(conn)] == ["20260924-120000"]


def test_schema_moves_end_time_back_to_start(conn):
    """Строка с концом прогона в `started` получает старт из результата; верная и повтор
    схемы не меняются (PQA-002)."""
    wrong = datetime(2026, 10, 19, 12, 25, 1, tzinfo=UTC)
    right = datetime(2026, 10, 19, 14, 21, 39, tzinfo=UTC)
    for name, start, stored in (("20261019-122501", wrong, wrong + timedelta(seconds=2220)),
                                ("20261019-142139", right, right)):
        payload = json.dumps({"started": start.isoformat(), "signals": []})
        jobs.save_report(conn, name, "q", stored, payload, "")
    jobs.apply_schema(conn)
    jobs.apply_schema(conn)
    got = {r["name"]: r["started"] for r in conn.execute("SELECT name, started FROM reports")}
    assert got == {"20261019-122501": wrong, "20261019-142139": right}


def test_events_come_back_in_order_after_the_cursor(conn):
    a = jobs.enqueue(conn, "q")
    jobs.emit(conn, a, 0.0, "stage", {"id": "expand"})
    jobs.emit(conn, a, 14.2, "facets", {"facets_ru": ["x", "y"]})
    jobs.emit(conn, a, 20.0, "stage", {"id": "science"})
    jobs.emit(conn, jobs.enqueue(conn, "чужое"), 1.0, "stage", {"id": "expand"})
    got = jobs.events(conn, a)
    assert [e["kind"] for e in got] == ["stage", "facets", "stage"]
    assert got[1]["data"]["facets_ru"] == ["x", "y"] and got[1]["at"] == pytest.approx(14.2)
    assert [e["data"]["id"] for e in jobs.events(conn, a, after=got[1]["seq"])] == ["science"]


def test_requeue_stale_drops_events_of_the_abandoned_run(conn):
    a = jobs.enqueue(conn, "q")
    jobs.claim(conn)
    jobs.emit(conn, a, 1.0, "stage", {"id": "expand"})
    jobs.requeue_stale(conn)
    assert jobs.events(conn, a) == []


def test_stage_typical_is_the_median_over_done_jobs(conn):
    assert jobs.stage_typical(conn) == {}
    for at in (100.0, 200.0, 400.0):
        j = jobs.enqueue(conn, f"q{at}")
        jobs.claim(conn)
        jobs.emit(conn, j, 0.0, "stage", {"id": "expand"})
        jobs.emit(conn, j, at, "stage", {"id": "money"})
        jobs.finish(conn, j, f"r{at}")
    running = jobs.enqueue(conn, "идёт")
    jobs.claim(conn)
    jobs.emit(conn, running, 9999.0, "stage", {"id": "money"})
    assert jobs.stage_typical(conn) == {"expand": 0.0, "money": 200.0}


def test_history_searches_by_substring_and_pages(conn):
    for i, q in enumerate(["технологии в ИИ", "финтех: 100% онлайн", "ИИ в медицине"]):
        started = datetime(2026, 9, 24 + i, 12, 0, tzinfo=UTC)
        jobs.save_report(conn, f"r{i}", q, started, json.dumps({"signals": []}), "")
    total, rows = jobs.summaries(conn, limit=1)
    assert total == 3 and [r["name"] for r in rows] == ["r2"]
    total, rows = jobs.summaries(conn, q="ии")
    assert total == 2 and [r["name"] for r in rows] == ["r2", "r0"]
    # «%» — буква запроса, а не шаблон LIKE
    assert jobs.summaries(conn, q="100%")[0] == 1 and jobs.summaries(conn, q="0%о")[0] == 0


def test_summaries_read_stored_summary_and_survive_odd_reports(conn):
    """Сводка пишется при сохранении; отчёт без полей или с не-массивом — нули, а не 500;
    отчёт, записанный без сводки, получает её при первом чтении."""
    result = {"signals": [{"confidence": 0.9}, {"confidence": 0.5}, {}], "excluded": [{}, {}],
              "thin": [{}], "facets": ["a", "b"], "candidates": 45, "sources_processed": 451,
              "seconds": 1389.5}
    started = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    jobs.save_report(conn, "full", "q", started, json.dumps(result), "")
    jobs.save_report(conn, "odd", "q", started.replace(day=11), json.dumps({"signals": None}), "")
    full, odd = jobs.summaries(conn)[1]
    assert {k: full[k] for k in ("signals", "confident", "excluded", "thin", "facets")} == {
        "signals": 3, "confident": 1, "excluded": 2, "thin": 1, "facets": 2}
    assert (full["candidates"], full["sources_processed"], full["seconds"]) == (45, 451, 1389.5)
    assert full["started"] == started.isoformat() and "result" not in full
    assert (odd["signals"], odd["confident"], odd["candidates"]) == (0, 0, 0)

    # как пишет код без колонки `summary` (стенд старой версии)
    conn.execute(
        "INSERT INTO reports (name, query, started, result, html) VALUES ('old', 'q', %s, %s, '')",
        [started.replace(day=10), json.dumps(result)],
    )
    old = jobs.summaries(conn)[1][2]
    assert old["name"] == "old" and old["signals"] == 3 and old["confident"] == 1
    stored = conn.execute("SELECT summary FROM reports WHERE name = 'old'").fetchone()["summary"]
    assert stored["candidates"] == 45


def test_prune_events_keeps_only_recent_done_jobs_and_live_ones(conn):
    done = []
    for i in range(3):
        j = jobs.enqueue(conn, f"q{i}")
        jobs.claim(conn)
        jobs.emit(conn, j, 0.0, "stage", {"id": "expand"})
        jobs.finish(conn, j, f"r{i}")
        done.append(j)
    running = jobs.enqueue(conn, "идёт")
    jobs.claim(conn)
    jobs.emit(conn, running, 1.0, "stage", {"id": "expand"})
    assert jobs.prune_events(conn, keep=2) == 1
    assert jobs.events(conn, done[0]) == [] and jobs.events(conn, done[2])
    assert jobs.events(conn, running)


def test_events_go_with_their_job(conn):
    j = jobs.enqueue(conn, "q")
    jobs.emit(conn, j, 0.0, "stage", {"id": "expand"})
    conn.execute("DELETE FROM jobs WHERE id = %s", [j])
    assert jobs.events(conn, j) == []


def test_prune_events_drops_cancelled_jobs(conn):
    # BR-47: отменённое задание тоже законченное — его события не копятся навсегда.
    j = jobs.enqueue(conn, "снят")
    jobs.emit(conn, j, 0.0, "stage", {"id": "expand"})
    jobs.cancelled(conn, j)
    assert jobs.prune_events(conn) == 1
    assert jobs.events(conn, j) == []
