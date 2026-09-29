"""Интерфейс радара слабых сигналов (Streamlit).

    streamlit run ui/app.py        # API_URL — адрес pelican.api, по умолчанию 127.0.0.1:8010

Тонкий слой: запрос уходит в API и встаёт в очередь, страница показывает место в
очереди, ход выполнения и готовую выдачу. Считает всё API; здесь только отображение.
"""

from __future__ import annotations

import html
import os
import time
from datetime import datetime

import httpx
import streamlit as st

API = os.environ.get("API_URL", "http://127.0.0.1:8010").rstrip("/")
EXAMPLES = (
    "технологии в ИИ",
    "перспективные решения в финтехе",
    "слабые сигналы в области кибербезопасности",
)

st.set_page_config(page_title="Радар слабых сигналов", page_icon="📡", layout="wide")


def client_ip() -> str:
    """IP пользователя. Снаружи стенд за Tailscale Funnel, и адрес соединения — это прокси;
    настоящий — левый в `X-Forwarded-For` (MDN: X-Forwarded-For)."""
    forwarded = st.context.headers.get("X-Forwarded-For") or ""
    ip = forwarded.split(",")[0].strip() or st.context.ip_address or ""
    return ip if isinstance(ip, str) else ""  # вне браузера (AppTest) контекст — заглушка


def api(method: str, path: str, **kw) -> httpx.Response:
    # Лимит запросов на IP считает API, а видит он только этот контейнер — адрес передаём.
    headers = {"X-Client-IP": client_ip()}
    return httpx.request(method, f"{API}{path}", timeout=30, headers=headers, **kw)


def submit(query: str) -> None:
    r = api("POST", "/api/ask", json={"query": query})
    if r.status_code == 200 and r.json().get("cached"):
        # Отчёт по этому запросу уже есть в истории — показываем его, а не ждём 15–20 минут.
        name = r.json()["report"]
        st.session_state.job = None
        st.session_state.report = name
        st.query_params.clear()
        st.query_params["report"] = name
        st.rerun()
    if r.status_code != 202:
        st.session_state.error = r.json().get("detail", r.text)
        return
    job = r.json()
    st.session_state.job = job["id"]
    st.session_state.report = None
    st.query_params["job"] = str(job["id"])
    st.rerun()  # перерисовать форму уже с неактивной кнопкой


SPINNER = """
<div style="display:flex;align-items:center;height:2.5rem">
  <div style="width:1.3rem;height:1.3rem;border-radius:50%;
              border:3px solid rgba(128,128,128,.3);border-top-color:#ff4b4b;
              animation:pelican-spin .8s linear infinite"></div>
</div>
<style>@keyframes pelican-spin{to{transform:rotate(360deg)}}</style>
"""


def smooth_bar(done_s: float, total_s: float, text: str) -> None:
    """Полоса, которую двигает браузер: CSS-анимация `linear` от текущей доли до 99 % за
    оставшееся время. Фрагмент перерисовывает её раз в 3 с с пересчитанной точки, а между
    перерисовками она идёт каждым кадром, а не ступенькой."""
    total_s = max(total_s, 1.0)
    start = min(done_s / total_s, 0.99)
    left_s = max(0.99 * total_s - done_s, 0.0)
    # Имя кадров уникально на отрисовку: иначе браузер не перезапустит анимацию с новой точки.
    name = f"pelican-bar-{time.monotonic_ns()}"
    track = "rgba(250,250,250,.15)" if st.context.theme.type == "dark" else "rgba(49,51,63,.12)"
    st.html(f"""
<div style="font-size:.9rem;margin-bottom:.35rem">{html.escape(text)}</div>
<div style="height:.5rem;border-radius:.25rem;background:{track};overflow:hidden">
  <div style="height:100%;width:{start:.4%};background:#ff4b4b;
              animation:{name} {left_s:.1f}s linear forwards"></div>
</div>
<style>@keyframes {name}{{from{{width:{start:.4%}}}to{{width:99%}}}}</style>
""")


def fmt_min(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"≈ {m} мин"


# ------------------------------------------------------------------ шапка

# Заголовок — ссылка на главную: полная загрузка `/` без `?report=`/`?job=` начинает новую
# сессию, и открыт ввод запроса. Идущее задание не теряется — очередь серверная.
st.markdown(
    '<h1><a href="/" target="_self" style="color:inherit;text-decoration:none">'
    "📡 Радар слабых сигналов</a></h1>",
    unsafe_allow_html=True,
)
st.caption(
    "Открытый запрос по технологическому направлению → ТОП-15 слабых сигналов"
    "с источниками, уверенностью и объяснением. Зрелые технологии, стандарты, хайп и шум "
    "исключаются с указанием причины."
)
st.info(
    "Стенд работает на одной локальной модели (одна GPU), поэтому запросы выполняются "
    "по одному. Если модель занята, ваш запрос встаёт в очередь и запустится сам. "
    "Новый запрос идёт 15–20 минут; запрос, который уже есть в истории, открывается сразу.",
    icon="ℹ️",
)

if "job" not in st.session_state:
    st.session_state.job = int(st.query_params["job"]) if "job" in st.query_params else None
    st.session_state.report = st.query_params.get("report")

# Пока запрос этой вкладки в очереди или в работе, второй не запускается.
busy = bool(st.session_state.job)

with st.form("ask", clear_on_submit=False):
    query = st.text_input(
        "Направление", placeholder="например: слабые сигналы в области кибербезопасности"
    )
    with st.container(horizontal=True, vertical_alignment="center"):
        pressed = st.form_submit_button("Найти слабые сигналы", type="primary", disabled=busy)
        if busy:
            st.html(SPINNER, width="content")
    if pressed and query.strip():
        submit(query.strip())

cols = st.columns(len(EXAMPLES))
for col, example in zip(cols, EXAMPLES, strict=True):
    if col.button(example, width="stretch", disabled=busy):
        submit(example)

if err := st.session_state.pop("error", None):
    st.error(err)


# ------------------------------------------------------------------ задание


@st.fragment(run_every="3s")
def job_panel(job_id: int) -> None:
    r = api("GET", f"/api/jobs/{job_id}")
    if r.status_code != 200:
        # Иначе кнопка так и осталась бы неактивной из-за несуществующего задания.
        st.session_state.error = "задание не найдено"
        st.session_state.job = None
        st.query_params.clear()
        st.rerun()
    j = r.json()
    st.subheader(f"Запрос: «{j['query']}»")
    if j["status"] == "queued":
        waited = (datetime.now().astimezone() - datetime.fromisoformat(j["created"])).total_seconds()
        eta = j["eta_start_s"]
        smooth_bar(waited, waited + eta, (
            f"В очереди: впереди {j['position']} "
            f"{'запрос' if j['position'] == 1 else 'запроса'} · начало {fmt_min(eta)}"
        ))
        st.caption("Страницу можно не держать открытой: запрос запустится сам, "
                   "ссылка на эту страницу сохраняет место в очереди.")
    elif j["status"] == "running":
        typical = j.get("typical_run_s") or 17 * 60
        elapsed = (datetime.now().astimezone() - datetime.fromisoformat(j["started"])).total_seconds()
        smooth_bar(elapsed, typical, f"Идёт анализ · {elapsed / 60:.0f} мин")
        with st.expander("Ход выполнения", expanded=True):
            st.code("\n".join(j["progress"][-15:]) or "…", language=None)
    elif j["status"] == "done":
        st.success("Готово")
        st.session_state.report = j["report"]
        st.session_state.job = None
        st.query_params.clear()
        st.query_params["report"] = j["report"]
        st.rerun()
    else:
        # Полный перезапуск страницы — чтобы кнопка снова стала активной.
        st.session_state.error = f"Запрос не выполнен: {j['error']}"
        st.session_state.job = None
        st.query_params.clear()
        st.rerun()


if st.session_state.job:
    job_panel(st.session_state.job)


# ------------------------------------------------------------------ выдача

def show_report(name: str) -> None:
    """Выдача — один отчёт-документ (`weak.page.render`, его отдаёт `/r/{name}`): запрос,
    статистика, ТОП строками-раскрывашками с инсайтом внутри, исключённые, модели ответа и
    «как считается». Второй вид той же выдачи здесь не рисуется — он только расходился бы
    с документом."""
    page = api("GET", f"/r/{name}")
    if page.status_code != 200:
        st.warning("отчёт не найден")
        return
    st.download_button("Скачать отчёт (HTML)", page.text, file_name=f"{name}.html",
                       mime="text/html")
    # Страницу собирает `weak.page.render`: все значения экранированы, скриптов нет.
    # Высота "content" — iframe растёт по документу, прокрутка одна — у страницы.
    st.iframe(page.text, height="content")


if st.session_state.get("report"):
    show_report(st.session_state.report)


# ------------------------------------------------------------------ история и очередь

st.divider()
left, right = st.columns([2, 1])
with left:
    st.subheader("История запросов")
    try:
        history = api("GET", "/api/reports").json()
    except httpx.HTTPError:
        history = []
    for h in history:
        label = (f"{h['query']} — {h['signals']} сигналов, уверенных {h['confident']} · "
                 f"{h['started'][:16].replace('T', ' ')}")
        if st.button(label, key=f"h-{h['name']}"):
            st.session_state.report = h["name"]
            st.query_params.clear()
            st.query_params["report"] = h["name"]
            st.rerun()
with right:
    st.subheader("Очередь")
    try:
        q = api("GET", "/api/queue").json()
    except httpx.HTTPError:
        q = []
    if not q:
        st.caption("пусто — новый запрос стартует сразу")
    for item in q:
        state = "идёт" if item["status"] == "running" else "ждёт"
        st.markdown(f"- {state}: {item['query']}")

