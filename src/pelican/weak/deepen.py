"""Углубление ТОП-15: досбор текстов, из которых карточка берёт факты.

Отбор и оценка идут по заголовкам — так они замерены. Карточке заголовка мало: из
«Toward Intelligent Agents with Learned World Models» нельзя написать, что сделано,
на чём проверено и с каким результатом, и модель пишет общие слова. Поэтому ПОСЛЕ
отбора, только для показанных, добирается:

- **аннотации работ** — пакетным запросом по id: arXiv export API (`id_list`) и
  OpenAlex (`filter=openalex:W…|W…`, аннотация — `abstract_inverted_index`). Запрос
  один на отчёт в каждую базу, а не на работу: бан arXiv по IP грозит бэкфиллу, не
  одному пакету (доступ проверен 2026-09-25: 200, < 1 с у обеих). Аннотация работы
  не меняется, поэтому кэш — бессрочный файл, а не суточный `weak.cache`;
- **фактовые заголовки** — один запрос в Google News на технологию, со словами
  раундов, запусков и пилотов: даты, суммы и имена в выдаче стоят именно в таких
  заголовках.

⚠️ В балл и в проверки уверенности добранное не идёт: оно приходит после них, как
патенты (`ask.run`), и состав свидетельств замеренных стадий не меняется.

⚠️ Отказ базы не роняет запрос: пустой ответ и строка в `errors`, как у `weak.live`.
"""

from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET

import httpx

from pelican.config import DATA_DIR, settings
from pelican.weak import live
from pelican.weak.live import LiveDoc

#: Аннотация в свидетельстве карточки. ⚠️ Догадка (docs/todo.md): длиннее — промпт
#: растёт на 15 карточек, короче — отрезается результат, который в аннотации обычно в конце.
ABSTRACT_CHARS = 900
#: Новых фактовых заголовков на технологию. ⚠️ Догадка (docs/todo.md).
DEEPEN_NEWS = 4
#: Слова, с которыми в заголовке стоят даты, суммы и имена.
FACT_WORDS = "(raises OR funding OR launches OR pilot OR deploys OR benchmark OR acquires)"
OPENALEX_BATCH = 50
CACHE = DATA_DIR / "abstracts.json"

_ARXIV = re.compile(r"arxiv\.org/abs/([^\s?#]+?)(?:v\d+)?$")
_OPENALEX = re.compile(r"openalex\.org/(W\d+)")
_ATOM = "{http://www.w3.org/2005/Atom}"
_WORD = re.compile(r"[a-z0-9]+")


def paper_key(url: str) -> str | None:
    """`arxiv:2309.04269` / `openalex:W4408841803` или None — у ссылки нет id базы."""
    if m := _ARXIV.search(url or ""):
        return f"arxiv:{m.group(1)}"
    if m := _OPENALEX.search(url or ""):
        return f"openalex:{m.group(1)}"
    return None


def parse_arxiv(xml: str) -> dict[str, str]:
    out = {}
    for entry in ET.fromstring(xml).iter(f"{_ATOM}entry"):
        key = paper_key((entry.findtext(f"{_ATOM}id") or "").strip())
        text = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())
        if key and text:
            out[key] = text
    return out


def openalex_text(inverted: dict[str, list[int]] | None) -> str:
    """`abstract_inverted_index` (слово → позиции) обратно в текст."""
    if not inverted:
        return ""
    at = {pos: word for word, places in inverted.items() for pos in places}
    return " ".join(at[i] for i in sorted(at))


def parse_openalex(payload: dict) -> dict[str, str]:
    out = {}
    for w in payload.get("results") or []:
        key = paper_key(str(w.get("id") or ""))
        text = openalex_text(w.get("abstract_inverted_index"))
        if key and text:
            out[key] = text
    return out


def _load_cache() -> dict[str, str]:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(got: dict[str, str]) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # кэш — ускорение, не условие


async def _fetch(keys: list[str], errors: dict[str, str]) -> dict[str, str]:
    arxiv = [k.split(":", 1)[1] for k in keys if k.startswith("arxiv:")]
    openalex = [k.split(":", 1)[1] for k in keys if k.startswith("openalex:")]
    got: dict[str, str] = {}
    async with httpx.AsyncClient(
        timeout=live.TIMEOUT_S, follow_redirects=True, headers={"User-Agent": settings.user_agent}
    ) as http:
        if arxiv:
            try:
                r = await http.get(
                    "https://export.arxiv.org/api/query",
                    params={"id_list": ",".join(arxiv), "max_results": len(arxiv)},
                )
                r.raise_for_status()
                got |= parse_arxiv(r.text)
            except (httpx.HTTPError, ET.ParseError) as e:
                errors["arxiv_abstracts"] = f"{type(e).__name__}: {str(e)[:120]}"
        for i in range(0, len(openalex), OPENALEX_BATCH):
            chunk = openalex[i : i + OPENALEX_BATCH]
            params = {
                "filter": "openalex:" + "|".join(chunk),
                "select": "id,abstract_inverted_index",
                "per_page": OPENALEX_BATCH,
            }
            if settings.openalex_api_key:
                params["api_key"] = settings.openalex_api_key
            try:
                r = await http.get("https://api.openalex.org/works", params=params)
                r.raise_for_status()
                got |= parse_openalex(r.json())
            except (httpx.HTTPError, ValueError) as e:
                errors["openalex_abstracts"] = f"{type(e).__name__}: {str(e)[:120]}"
    return got


def abstracts(urls: list[str], errors: dict[str, str]) -> dict[str, str]:
    """Ссылка работы → аннотация. Нет аннотации (у OpenAlex пусто часто) — ссылки нет."""
    keys = {u: k for u in urls if (k := paper_key(u))}
    cache = _load_cache()
    missing = sorted({k for k in keys.values() if k not in cache})
    if missing:
        fetched = asyncio.run(_fetch(missing, errors))
        # Пустая аннотация тоже кэшируется, но только когда база ответила.
        failed = {"arxiv": "arxiv_abstracts" in errors, "openalex": "openalex_abstracts" in errors}
        for k in missing:
            if k in fetched or not failed[k.split(":", 1)[0]]:
                cache[k] = fetched.get(k, "")
        _save_cache(cache)
    return {u: cache[k] for u, k in keys.items() if cache.get(k)}


def _about(title: str, core: str) -> bool:
    """Заголовок про эту технологию: в нём есть каждое слово ядра (по первым 5 буквам —
    «model» ловит «models»). Без этого «world models raises» приносит раунды вообще."""
    words = [w[:5] for w in _WORD.findall(core.lower()) if len(w) > 2]
    head = title.lower()
    return bool(words) and all(w in head for w in words)


def fact_news(cores: list[str], seen: list[set[str]]) -> list[list[LiveDoc]]:
    """На каждое ядро — до `DEEPEN_NEWS` новых заголовков про него, которых нет в `seen`."""
    got = live.news_many([f"{c} {FACT_WORDS}" for c in cores], "en")
    out = []
    for core, docs, titles in zip(cores, got, seen, strict=True):
        fresh = []
        for d in docs:
            if d.title not in titles and _about(d.title, core):
                fresh.append(d)
                titles.add(d.title)
            if len(fresh) >= DEEPEN_NEWS:
                break
        out.append(fresh)
    return out
