"""Очередь запросов и отчёты: живой Postgres, отдельная тестовая база."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from pelican import jobs


@pytest.fixture
def conn(pg_url):
    c = jobs.connect(pg_url)
    jobs.apply_schema(c)
    c.execute("TRUNCATE jobs, reports RESTART IDENTITY")
    yield c
    c.close()


def test_fifo_positions_and_autostart_order(conn):
    a, b, c = (jobs.enqueue(conn, q) for q in ("первый", "второй", "третий"))
    assert [jobs.snapshot(conn, i)["position"] for i in (a, b, c)] == [0, 1, 2]

    got = jobs.claim(conn)
    assert got.id == a
    # Идущий считается: второй — «один впереди», третий — «два впереди».
    assert jobs.snapshot(conn, a)["status"] == "running"
    assert [jobs.snapshot(conn, i)["position"] for i in (b, c)] == [1, 2]

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
    def attempt(_):
        with jobs.connect(pg_url) as c:
            try:
                return jobs.enqueue(c, "гонка", "10.0.0.2")
            except jobs.Busy:
                return None

    with ThreadPoolExecutor(8) as pool:
        got = [r for r in pool.map(attempt, range(8)) if r is not None]
    assert len(got) == jobs.MAX_ACTIVE_PER_CLIENT


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
