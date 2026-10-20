"""API очереди с поддельным прогоном: порядок, автозапуск следующего, отказ в статусе."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from pelican import api, events, jobs
from pelican.weak.ask import REPORT_VERSION


class FakeRunner:
    """Прогон, который ждёт разрешения — чтобы очередь было видно."""

    def __init__(self) -> None:
        self.go = threading.Event()
        self.seen: list[str] = []

    def __call__(self, query, say, emit):
        self.seen.append(query)
        say("стадия")
        if query == "упади":
            raise RuntimeError("модель недоступна")
        assert self.go.wait(10)
        name = f"20261020-1200{len(self.seen):02d}"  # имя по BR-45: иное /api/reports не отдаёт
        result = {"query": query, "signals": [{"confidence": 1.0}], "version": REPORT_VERSION}
        return name, json.dumps(result), "<p>ok</p>"


@pytest.fixture
def setup(pg_url):
    with jobs.connect(pg_url) as c:
        jobs.apply_schema(c)
        c.execute("TRUNCATE jobs, reports, job_events RESTART IDENTITY")
    runner = FakeRunner()
    app = api.create_app(pg_url, runner)
    yield TestClient(app), runner
    app.state.worker.stop.set()
    runner.go.set()
    app.state.pool.close()


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
    assert client.get(f"/api/reports/{done_b['report']}").status_code == 200
    assert client.get("/api/history").json()["items"][0]["confident"] == 1


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
    # Человеку — фраза с действием, сырой текст исключения — отдельно.
    assert snap["error"].startswith("Прогон остановился") and "Повторите" in snap["error"]
    assert snap["error_detail"] == "RuntimeError: модель недоступна"
    ev = client.get(f"/api/jobs/{j['id']}/events").json()
    assert ev["error"] == snap["error"] and ev["error_detail"] == snap["error_detail"]


def test_three_active_per_ip_fourth_rejected(setup):
    client, runner = setup
    ip = {"X-Forwarded-For": "203.0.113.7"}
    first = client.post("/api/ask", json={"query": "раз"}, headers=ip).json()
    _wait(client, first["id"], "running")
    for q in ("два", "три"):
        assert client.post("/api/ask", json={"query": q}, headers=ip).status_code == 202

    extra = client.post("/api/ask", json={"query": "четыре"}, headers=ip)
    assert extra.status_code == 429 and "3 запроса" in extra.json()["detail"]
    # Чужой адрес лимит не задевает.
    other = client.post("/api/ask", json={"query": "чужой"}, headers={"X-Forwarded-For": "198.51.100.1"})
    assert other.status_code == 202

    runner.go.set()
    _wait(client, first["id"], "done")
    assert client.post("/api/ask", json={"query": "четыре"}, headers=ip).status_code == 202


def test_short_query_rejected(setup):
    client, _ = setup
    assert client.post("/api/ask", json={"query": "ai"}).status_code == 422


def test_forwarded_for_limits_funnel_clients(setup):
    """Через Funnel адрес клиента — левый в X-Forwarded-For; подставной X-Client-IP лимит не
    обходит (Funnel его не перезаписывает, и API его не читает)."""
    client, runner = setup
    fwd = {"X-Forwarded-For": "203.0.113.9, 100.64.0.1"}
    first = client.post("/api/ask", json={"query": "раз"}, headers=fwd).json()
    _wait(client, first["id"], "running")
    for q in ("два", "три"):
        assert client.post("/api/ask", json={"query": q}, headers=fwd).status_code == 202
    assert client.post("/api/ask", json={"query": "четыре"}, headers=fwd).status_code == 429
    spoof = {**fwd, "X-Client-IP": "198.51.100.77"}
    assert client.post("/api/ask", json={"query": "пять"}, headers=spoof).status_code == 429
    runner.go.set()


def test_queue_shows_own_queries_only(setup):
    """Очередь видна всем местом, текстом и ссылкой — только автору (PQA-001, NFR-52)."""
    client, runner = setup
    me, other = {"X-Forwarded-For": "203.0.113.7"}, {"X-Forwarded-For": "198.51.100.1"}
    a = client.post("/api/ask", json={"query": "чужой запрос про банки"}, headers=other).json()
    _wait(client, a["id"], "running")
    b = client.post("/api/ask", json={"query": "мой запрос"}, headers=me).json()

    mine = client.get("/api/queue", headers=me).json()
    assert [(x["status"], x["position"], x["mine"]) for x in mine] == [("running", 0, False), ("queued", 1, True)]
    assert "query" not in mine[0] and "id" not in mine[0]
    assert mine[1]["query"] == "мой запрос" and mine[1]["id"] == b["id"]
    # Ожидаемое начало — то же, что в снимке задания.
    assert mine[0]["eta_start_s"] == 0.0 and mine[1]["eta_start_s"] == pytest.approx(b["eta_start_s"], abs=2)
    theirs = client.get("/api/queue", headers=other).json()
    assert theirs[0]["query"] == "чужой запрос про банки" and "query" not in theirs[1]
    runner.go.set()


class StagedRunner(FakeRunner):
    """Прогон из двух стадий с паузой между ними — граница, на которой снимается отмена."""

    def __call__(self, query, say, emit):
        self.seen.append(query)
        emit("stage", events.stage("expand"))
        assert self.go.wait(10)
        emit("stage", events.stage("science"))
        result = {"query": query, "signals": [], "version": REPORT_VERSION}
        return f"20261020-1300{len(self.seen):02d}", json.dumps(result), ""


def test_cancel_queued_now_running_at_stage_boundary(pg_url):
    with jobs.connect(pg_url) as c:
        jobs.apply_schema(c)
        c.execute("TRUNCATE jobs, reports, job_events RESTART IDENTITY")
    runner = StagedRunner()
    app = api.create_app(pg_url, runner)
    client = TestClient(app)
    me = {"X-Forwarded-For": "203.0.113.7"}
    try:
        a = client.post("/api/ask", json={"query": "ошибочный запрос"}, headers=me).json()
        _wait(client, a["id"], "running")
        b = client.post("/api/ask", json={"query": "второй ошибочный"}, headers=me).json()
        # Чужой адрес отменить не может.
        assert client.post(f"/api/jobs/{b['id']}/cancel", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 403
        assert client.post(f"/api/jobs/{b['id']}/cancel", headers=me).json()["status"] == "cancelled"
        # Идущее — флаг, статус `running` до границы стадии.
        assert client.post(f"/api/jobs/{a['id']}/cancel", headers=me).json()["status"] == "running"
        assert client.get(f"/api/jobs/{a['id']}").json()["cancel"] is True
        runner.go.set()
        _wait(client, a["id"], "cancelled")
        assert runner.seen == ["ошибочный запрос"]  # снятое из очереди не стартовало
        assert client.get("/api/queue", headers=me).json() == []
        assert client.post("/api/jobs/999/cancel", headers=me).status_code == 404
        # Отменённые освобождают слот лимита: снова три подряд.
        for q in ("раз", "два", "три"):
            assert client.post("/api/ask", json={"query": q}, headers=me).status_code == 202
    finally:
        app.state.worker.stop.set()
        runner.go.set()


def test_cross_site_post_rejected_own_page_and_scripts_pass(setup):
    """PQA-020: чужая страница не ставит и не снимает задания из браузера пользователя."""
    client, runner = setup
    me = {"X-Forwarded-For": "203.0.113.9"}
    job = client.post("/api/ask", json={"query": "свой запрос"}, headers=me).json()
    foreign = [
        {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "same-site", "Origin": "https://evil.testserver"},
        {"Origin": "https://evil.example"},  # браузер без Fetch Metadata
        {"Origin": "null"},
    ]
    for h in foreign:
        assert client.post(f"/api/jobs/{job['id']}/cancel", headers={**me, **h}).status_code == 403, h
        assert client.post("/api/ask", json={"query": "чужой запрос"}, headers={**me, **h}).status_code == 403, h
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] in ("queued", "running")
    # Чтение с чужой страницы не трогаем — читать ответ ей не даёт одноисточниковость.
    assert client.get("/api/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    own = [
        {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"},
        {"Origin": "http://testserver"},
        {"Origin": "https://stand.example", "X-Forwarded-Host": "stand.example"},
    ]
    for h in own:
        assert client.post("/api/ask", json={"query": "свой запрос"}, headers={**me, **h}).status_code in (200, 202), h
    assert client.post(f"/api/jobs/{job['id']}/cancel", headers=me).status_code == 200   # скрипт без заголовков


def test_security_headers_on_api_and_pages(setup, tmp_path, monkeypatch, pg_url):
    """QP-07: заголовки безопасности и у API, и у страниц интерфейса."""
    client, _ = setup
    (tmp_path / "index.html").write_text("<p>ok</p>")
    (tmp_path / "print.html").write_text("<p>print</p>")
    monkeypatch.setattr(api, "WEB_CANDIDATES", (str(tmp_path),))
    site = api.create_app(pg_url, None)
    try:
        pages = TestClient(site)
        for got in (client.get("/api/health"), pages.get("/"), pages.get("/print.html")):
            assert got.status_code == 200
            for name, value in api.SECURITY_HEADERS.items():
                assert got.headers[name] == value
    finally:
        site.state.pool.close()


def test_report_name_outside_br45_is_404(setup):
    client, _ = setup
    for name in ("x%00y", "..%2Fx", "abc", "20261020-12000"):
        assert client.get(f"/api/reports/{name}").status_code == 404, name


class EventRunner(FakeRunner):
    """Прогон с хуком событий: как настоящий `runner.run_query(query, say, emit=...)`."""

    def __call__(self, query, say, emit):
        emit("stage", {"id": "expand", "n": 1, "of": 10})
        emit("facets", {"facets_ru": ["платежи", "скоринг"]})
        emit("verdict", {"label": "agent mandates", "kind": "emerging", "facet": 0})
        return super().__call__(query, say, emit)


def test_events_endpoint_replays_the_run_with_a_cursor(pg_url):
    with jobs.connect(pg_url) as c:
        jobs.apply_schema(c)
        c.execute("TRUNCATE jobs, reports, job_events RESTART IDENTITY")
    runner = EventRunner()
    app = api.create_app(pg_url, runner)
    client = TestClient(app)
    try:
        j = client.post("/api/ask", json={"query": "платежи агентов"}).json()
        _wait(client, j["id"], "running")
        assert "stage_typical" in client.get(f"/api/jobs/{j['id']}").json()
        first = client.get(f"/api/jobs/{j['id']}/events").json()
        assert first["status"] == "running" and first["elapsed"] is not None
        # вид события — в `t`, а `kind` вердикта (жанр кандидата) не затирается
        assert [e["t"] for e in first["events"]] == ["stage", "facets", "verdict"]
        assert first["events"][2]["kind"] == "emerging"
        runner.go.set()
        done = _wait(client, j["id"], "done")
        rest = client.get(f"/api/jobs/{j['id']}/events", params={"after": first["next"]}).json()
        assert [e["t"] for e in rest["events"]] == ["stage", "done"]
        assert rest["events"][-1]["report"] == done["report"] == rest["report"]
        assert client.get("/api/jobs/999/events").status_code == 404
    finally:
        app.state.worker.stop.set()
        runner.go.set()


def test_history_endpoint_has_total_search_and_summary(setup, pg_url):
    client, _ = setup
    with jobs.connect(pg_url) as c:
        for i, q in enumerate(["технологии в ИИ", "кибербезопасность"]):
            result = {"signals": [{"confidence": 0.9}], "excluded": [{}, {}], "seconds": 900.0,
                      "facets": ["a", "b", "c"]}
            jobs.save_report(c, f"r{i}", q, datetime.now().astimezone(), json.dumps(result), "")
    got = client.get("/api/history", params={"q": "ИИ"}).json()
    assert got["total"] == 1
    item = got["items"][0]
    assert item["query"] == "технологии в ИИ" and item["excluded"] == 2
    assert item["seconds"] == 900.0 and item["facets"] == 3 and item["confident"] == 1
    # длительность для обещаний — из jobs, как у экрана ожидания, а не по показанным отчётам
    with jobs.connect(pg_url) as c:
        assert got["typical_run_s"] == round(jobs.typical_run_s(c), 1)


def test_saved_report_stage_follows_ladder_in_every_list():
    # BR-15: старый отчёт хранит у исключённых ступень модели — экран показывает лестницу,
    # как и HTML (`page.from_json`); ступень без вех и свежая ступень не меняются.
    old = {
        "signals": [{"stage": "пилот", "judgments": {"pilot_with_customer": 1.0}}],
        "excluded": [{"stage": "раннее внедрение", "judgments": {"prototype_built": 1.0}}],
        "watch": [{"stage": "пилот", "judgments": {"generally_available": 0.0,
                                                   "prototype_built": 0.0}}],
        "thin": [{"stage": "прототип/poc", "judgments": {}}],
    }
    got = api._laddered(old)
    assert got["signals"][0]["stage"] == "пилот"
    assert got["excluded"][0]["stage"] == "прототип/poc"
    assert got["watch"][0]["stage"] == "концепция/исследование"
    assert got["thin"][0]["stage"] == "прототип/poc"


def test_glossary_serves_the_report_page_texts(setup):
    from pelican.weak import page as page_mod

    client, _ = setup
    got = client.get("/api/glossary").json()
    assert got["hints"] == page_mod.HINTS and {"confidence", "candidates", "thin", "sources"} <= got["hints"].keys()
    assert got["live_names"]["patents"] == page_mod.LIVE_NAMES["patents"]


def test_system_exit_in_runner_fails_job_and_keeps_worker(setup):
    """`SystemExit` из глубины прогона — отказ задания, а не молча мёртвый поток."""
    client, runner = setup

    def exits(query, say, emit):
        raise SystemExit("нет шардов")

    app_worker = client.app.state.worker
    app_worker.runner = exits
    j = client.post("/api/ask", json={"query": "без шардов"}).json()
    snap = _wait(client, j["id"], "failed")
    assert snap["error_detail"] == "SystemExit: нет шардов" and snap["error"]
    assert app_worker.alive()
    assert client.get("/api/health").json()["worker"] is True


def test_lost_connection_reconnects(setup, monkeypatch):
    """Обрыв Postgres в `claim` — переподключение, а не мёртвый поток (PEL-I-02)."""
    import psycopg

    client, runner = setup
    monkeypatch.setattr(api, "RECONNECT_S", 0.05)
    real_claim, calls = jobs.claim, []

    def flaky(conn):
        calls.append(1)
        if len(calls) == 1:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        return real_claim(conn)

    monkeypatch.setattr(api.jobs, "claim", flaky)
    client.app.state.worker.wake.set()
    runner.go.set()
    j = client.post("/api/ask", json={"query": "после обрыва"}).json()
    _wait(client, j["id"], "done")
    assert client.app.state.worker.alive()


def test_watchdog_fails_job_without_progress(setup, pg_url):
    client, runner = setup
    j = client.post("/api/ask", json={"query": "зависший"}).json()
    _wait(client, j["id"], "running")
    with jobs.connect(pg_url) as c:
        c.execute("UPDATE jobs SET touched = now() - interval '1 hour' WHERE id = %s", [j["id"]])
        assert jobs.fail_stuck(c, 600) == [j["id"]]
    assert "сторожем" in client.get(f"/api/jobs/{j['id']}").json()["error"]
    runner.go.set()


def test_watchdog_restarts_dead_worker(setup):
    client, runner = setup
    worker = client.app.state.worker
    worker.stop.set()
    worker.wake.set()
    worker.thread.join(5)
    assert client.get("/api/health").status_code == 503
    worker.stop.clear()
    worker.check()
    assert worker.alive() and worker.restarts == 1
    runner.go.set()
    j = client.post("/api/ask", json={"query": "после перезапуска"}).json()
    _wait(client, j["id"], "done")


def test_text_responses_gzipped_when_client_accepts(setup):
    client, _ = setup
    got = client.get("/api/glossary", headers={"Accept-Encoding": "gzip"})
    assert got.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in got.headers["vary"].lower()
    assert got.json()["hints"]
    plain = client.get("/api/glossary", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers


def test_static_hashed_assets_immutable_entry_points_revalidated(tmp_path):
    from fastapi import FastAPI

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "main-AbC123.js").write_text("x")
    (tmp_path / "index.html").write_text("<p>ok</p>")
    app = FastAPI()
    app.mount("/", api._SiteStatic(directory=tmp_path, html=True))
    client = TestClient(app)
    assert client.get("/assets/main-AbC123.js").headers["cache-control"] == "public, max-age=31536000, immutable"
    for path in ("/", "/index.html"):
        got = client.get(path)
        assert got.headers["cache-control"] == "no-cache"
        again = client.get(path, headers={"If-None-Match": got.headers["etag"]})
        assert again.status_code == 304 and again.headers["cache-control"] == "no-cache"
