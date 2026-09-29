"""API очереди с поддельным прогоном: порядок, автозапуск следующего, отказ в статусе."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from pelican import api, jobs
from pelican.weak.ask import REPORT_VERSION


class FakeRunner:
    """Прогон, который ждёт разрешения — чтобы очередь было видно."""

    def __init__(self) -> None:
        self.go = threading.Event()
        self.seen: list[str] = []

    def __call__(self, query, say):
        self.seen.append(query)
        say("стадия")
        if query == "упади":
            raise RuntimeError("модель недоступна")
        assert self.go.wait(10)
        name = f"r{len(self.seen)}"
        result = {"query": query, "signals": [{"confidence": 1.0}], "version": REPORT_VERSION}
        return name, json.dumps(result), "<p>ok</p>"


@pytest.fixture
def setup(pg_url):
    with jobs.connect(pg_url) as c:
        jobs.apply_schema(c)
        c.execute("TRUNCATE jobs, reports RESTART IDENTITY")
    runner = FakeRunner()
    app = api.create_app(pg_url, runner)
    yield TestClient(app), runner
    app.state.worker.stop.set()
    runner.go.set()


def _wait(client, job_id, status, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = client.get(f"/api/jobs/{job_id}").json()
        if snap["status"] == status:
            return snap
        time.sleep(0.05)
    raise AssertionError(f"задание {job_id} не дошло до {status}: {snap}")


def test_second_request_waits_then_autostarts(setup):
    client, runner = setup
    a = client.post("/api/ask", json={"query": "первый запрос"})
    assert a.status_code == 202
    _wait(client, a.json()["id"], "running")

    b = client.post("/api/ask", json={"query": "второй запрос"}).json()
    assert b["status"] == "queued" and b["position"] == 1 and b["eta_start_s"] > 0

    runner.go.set()
    _wait(client, a.json()["id"], "done")
    done_b = _wait(client, b["id"], "done")
    assert runner.seen == ["первый запрос", "второй запрос"]
    assert client.get(f"/r/{done_b['report']}").text == "<p>ok</p>"
    assert client.get("/api/reports").json()[0]["confident"] == 1


def test_repeat_query_served_from_history(setup):
    client, runner = setup
    a = client.post("/api/ask", json={"query": "технологии в ИИ"}).json()
    _wait(client, a["id"], "running")
    # Тот же запрос, пока первый идёт, — то же задание, а не второе в очереди.
    same = client.post("/api/ask", json={"query": "  Технологии  в ии "}).json()
    assert same["id"] == a["id"] and len(client.get("/api/queue").json()) == 1

    runner.go.set()
    done = _wait(client, a["id"], "done")
    again = client.post("/api/ask", json={"query": "технологии в ИИ"})
    assert again.status_code == 200
    assert again.json() == {"status": "done", "query": "технологии в ИИ",
                            "report": done["report"], "cached": True}
    assert runner.seen == ["технологии в ИИ"]


def test_stale_report_version_not_served_from_history(setup, pg_url):
    """Отчёт, собранный до исправлений выдачи, повтор не отдаёт — ставит новый прогон."""
    client, runner = setup
    with jobs.connect(pg_url) as c:
        old = json.dumps({"query": "необанк", "signals": []})  # без `version` — до версий
        jobs.save_report(c, "old", "необанк", datetime.now().astimezone(), old, "<p>old</p>")
    again = client.post("/api/ask", json={"query": "необанк"}).json()
    assert again["status"] in ("queued", "running")
    runner.go.set()


def test_failure_visible_in_status(setup):
    client, _ = setup
    j = client.post("/api/ask", json={"query": "упади"}).json()
    snap = _wait(client, j["id"], "failed")
    assert "модель недоступна" in snap["error"]


def test_three_active_per_ip_fourth_rejected(setup):
    client, runner = setup
    ip = {"X-Client-IP": "203.0.113.7"}
    first = client.post("/api/ask", json={"query": "раз"}, headers=ip).json()
    _wait(client, first["id"], "running")
    for q in ("два", "три"):
        assert client.post("/api/ask", json={"query": q}, headers=ip).status_code == 202

    extra = client.post("/api/ask", json={"query": "четыре"}, headers=ip)
    assert extra.status_code == 429 and "3 запроса" in extra.json()["detail"]
    # Чужой адрес лимит не задевает.
    other = client.post("/api/ask", json={"query": "чужой"}, headers={"X-Client-IP": "198.51.100.1"})
    assert other.status_code == 202

    runner.go.set()
    _wait(client, first["id"], "done")
    assert client.post("/api/ask", json={"query": "четыре"}, headers=ip).status_code == 202


def test_short_query_rejected(setup):
    client, _ = setup
    assert client.post("/api/ask", json={"query": "ai"}).status_code == 422
