"""Источники шире IT: гранты, препринты наук о жизни, клинические испытания, стартапы.

Эксперты заказчика (консультация 16.10): «Почему вы ограничились Хабром, GitHub,
КиберЛенинкой и Google News? Не думаете, что что-то упускаете?» и «блок технологического
лидерства — биотех, перспективная энергетика, широко смотрите». Каждый источник ниже
отвечает на свою часть определения слабого сигнала (`weak.layers`):

| источник | что подтверждает | проверка доступа 2026-10-16 |
|---|---|---|
| NIH RePORTER `POST /v2/projects/search` | деньги на исследование — раньше статьи и раунда | 200, по теме |
| Europe PMC, `SRC:PPR` | препринты bioRxiv / medRxiv / Research Square / ChemRxiv | 200, по теме |
| ClinicalTrials.gov `/api/v2/studies`, `query.titles` | переход биотеха из лаборатории в пилот | 200, по теме |
| каталог YC (`yc-oss.github.io/api/companies/all.json`) | стартап ранней стадии делает это | 200, 6 307 компаний |

Гранты как ранний индикатор темы — признанный приём: портфельный анализ финансирования
(topic prominence по STAR METRICS, Boyack и др., arXiv:1709.03453; темы NCI 2000–2020,
arXiv:2306.13075). ⚠️ Отвергнуто проверкой: NSF Awards API — поиск `keyword` вольный (на
«organoid microfluidic» первым отдал укрепление грунта белками); Common Crawl, живой
OpenAlex — см. `weak.live`.

⚠️ **Три исхода, как у новостей** (`live.news_many_measured`): отказ источника — «не
проверено» (`None`), а не «ничего нет»; иначе слой «наблюдать» получал бы кандидатов за
сбой чужого API.
⚠️ **Срез времени**: у каждого API своё окно дат (`_window`), а всё из будущего режет общая
дверь `live._cached` по дате документа. Каталог YC — сегодняшний: компании, запущенные
позже среза, отбрасываются по `launched_at`, но умершие до сегодняшнего дня в нём уже не
видны (survivorship bias, как у новостей — `weak.asof`).
⚠️ Включается `WEAK_EXTRA_SOURCES` (`weak.flags`); замер — pelican/docs/final-measure.md.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx

from pelican.config import settings
from pelican.weak import asof
from pelican.weak.live import TIMEOUT_S, LiveDoc, _cached, _clean

#: Сколько документов источника на кандидата.
PER_SOURCE = 3
#: Окно поиска до «сегодня» прогона — то же, что у новостей (`asof.WINDOW_DAYS`).
WINDOW_DAYS = asof.WINDOW_DAYS
#: Каталог YC перекачивается раз в сутки.
YC_URL = "https://yc-oss.github.io/api/companies/all.json"
YC_MAX_AGE_S = 24 * 3600

NIH = "nih_reporter"
PREPRINT = "preprint"
TRIALS = "clinicaltrials"
YC = "yc"
SOURCES = (NIH, PREPRINT, TRIALS, YC)


@dataclass(slots=True)
class Extra:
    """Найденное по одному кандидату: источник → документы или `None` (не ответил)."""

    docs: dict[str, list[LiveDoc] | None] = field(default_factory=dict)

    def found(self, source: str) -> list[LiveDoc]:
        return self.docs.get(source) or []

    def unmeasured(self) -> list[str]:
        return [s for s, d in self.docs.items() if d is None]


def _window() -> tuple[date, date]:
    end = asof.today()
    return end - timedelta(days=WINDOW_DAYS), end


# ------------------------------------------------------------------ разбор ответов


def parse_nih(payload: dict) -> list[LiveDoc]:
    out = []
    for r in payload.get("results") or []:
        amount = r.get("award_amount")
        org = (r.get("organization") or {}).get("org_name") or ""
        out.append(
            LiveDoc(
                NIH,
                _clean(r.get("project_title") or ""),
                f"https://reporter.nih.gov/project-details/{r.get('appl_id', '')}",
                (r.get("award_notice_date") or "")[:10],
                f"грант NIH, {org}".strip(", "),
                "en",
                f"грант NIH ${amount:,}".replace(",", " ") if isinstance(amount, int) else "",
                domain="reporter.nih.gov",
            )
        )
    return out


def _publisher_domain(name: str) -> str:
    """Домен сервера препринтов по имени издателя у Europe PMC — для доверенности."""
    low = name.lower()
    for key, dom in (
        ("biorxiv", "biorxiv.org"),
        ("medrxiv", "medrxiv.org"),
        ("chemrxiv", "chemrxiv.org"),
        ("research square", "researchsquare.com"),
        ("preprints.org", "preprints.org"),
        ("ssrn", "ssrn.com"),
        ("arxiv", "arxiv.org"),
    ):
        if key in low:
            return dom
    return "europepmc.org"


def parse_europepmc(payload: dict) -> list[LiveDoc]:
    out = []
    for r in (payload.get("resultList") or {}).get("result") or []:
        publisher = (r.get("bookOrReportDetails") or {}).get("publisher") or r.get("journalTitle") or ""
        doi = r.get("doi") or ""
        url = f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/PPR/{r.get('id', '')}"
        out.append(
            LiveDoc(
                PREPRINT,
                _clean(r.get("title") or ""),
                url,
                (r.get("firstPublicationDate") or "")[:10],
                publisher,
                "en",
                _clean(r.get("authorString") or "")[:200],
                domain=_publisher_domain(publisher),
            )
        )
    return out


def parse_trials(payload: dict) -> list[LiveDoc]:
    out = []
    for s in payload.get("studies") or []:
        p = s.get("protocolSection") or {}
        ident = p.get("identificationModule") or {}
        status = p.get("statusModule") or {}
        sponsor = ((p.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name", "")
        phases = ", ".join((p.get("designModule") or {}).get("phases") or [])
        start = (status.get("startDateStruct") or {}).get("date") or ""
        nct = ident.get("nctId", "")
        out.append(
            LiveDoc(
                TRIALS,
                _clean(ident.get("briefTitle") or ""),
                f"https://clinicaltrials.gov/study/{nct}",
                start[:10],
                sponsor,
                "en",
                " · ".join(x for x in (phases, status.get("overallStatus", "")) if x),
                domain="clinicaltrials.gov",
            )
        )
    return out


_WORD = re.compile(r"[a-z0-9]+")
#: Слова, по которым компанию не ищут: они есть в описании почти любой.
_STOP = frozenset(
    ["a", "an", "and", "for", "of", "the", "to", "in", "on", "with", "by", "based", "using", "via", "ai", "platform", "platforms", "system", "systems", "technology", "technologies", "solution", "solutions"]
)


def _fold(word: str) -> str:
    """Множественное число к единственному — S-stemmer (Harman 1991): «organoids» и
    «organoid» — одно слово. Без него описание «screening drugs against organoids» не
    находилось по «organoid»; более сильный стеммер склеивал бы разные слова."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    return [_fold(w) for w in _WORD.findall(text.lower())]


def _contains(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def search_yc(companies: list[dict], phrase: str, until: date, limit: int = PER_SOURCE) -> list[LiveDoc]:
    """Компании каталога YC, в описании которых фраза стоит ЦЕЛИКОМ, слово за словом, и
    которые запущены не позже `until`.

    ⚠️ Слова фразы обязаны идти подряд: по набору слов «self-attention» находил кружок
    программирования для подростков, а «solid-state battery» — микродвигатели для дронов
    (проверка 2026-10-16). Фразы меньше чем из двух значимых слов не ищутся: одно общее
    слово ловит всё подряд.
    """
    need = _tokens(phrase)
    if len([w for w in need if len(w) > 2 and w not in _STOP]) < 2:
        return []
    out = []
    for c in companies:
        text = f"{c.get('one_liner') or ''} {c.get('long_description') or ''}"
        if not _contains(_tokens(text), need):
            continue
        launched = c.get("launched_at")
        day = datetime.fromtimestamp(launched, UTC).date() if isinstance(launched, int | float) else None
        if day and day > until:
            continue
        out.append(
            LiveDoc(
                YC,
                f"{c.get('name', '')}: {_clean(c.get('one_liner') or '')}",
                c.get("url") or c.get("website") or "",
                day.isoformat() if day else "",
                f"Y Combinator {c.get('batch') or ''}".strip(),
                "en",
                _clean(c.get("long_description") or "")[:300],
                domain="ycombinator.com",
            )
        )
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ запросы


async def _fetch_nih(http: httpx.AsyncClient, phrase: str) -> list[LiveDoc]:
    start, end = _window()
    r = await http.post(
        "https://api.reporter.nih.gov/v2/projects/search",
        json={
            "criteria": {
                # ⚠️ Фраза — в кавычках: без них NIH ищет слова порознь, и на
                # «organ-on-a-chip» первым отдавал стимуляцию блуждающего нерва (проверка
                # 2026-10-16; в кавычках — органы-на-чипе).
                "advanced_text_search": {
                    "operator": "and",
                    "search_field": "projecttitle,abstracttext",
                    "search_text": f'"{phrase}"',
                },
                "award_notice_date": {"from_date": start.isoformat(), "to_date": end.isoformat()},
            },
            "include_fields": ["ApplId", "ProjectTitle", "AwardAmount", "Organization",
                               "AwardNoticeDate"],
            "offset": 0,
            "limit": PER_SOURCE,
            "sort_field": "award_notice_date",
            "sort_order": "desc",
        },
    )
    r.raise_for_status()
    return parse_nih(r.json())


async def _fetch_preprints(http: httpx.AsyncClient, phrase: str) -> list[LiveDoc]:
    start, end = _window()
    q = f'"{phrase}" AND SRC:PPR AND FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}]'
    r = await http.get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={quote(q)}&format=json&pageSize={PER_SOURCE}&resultType=lite"
        "&sort=P_PDATE_D%20desc"
    )
    r.raise_for_status()
    return parse_europepmc(r.json())


async def _fetch_trials(http: httpx.AsyncClient, phrase: str) -> list[LiveDoc]:
    start, end = _window()
    window = f"AREA[StartDate]RANGE[{start.isoformat()},{end.isoformat()}]"
    # ⚠️ Фраза в кавычках, как у NIH: без них «self-attention» находил «Self-Activation in
    # Attention Deficit…» (проверка 2026-10-16).
    r = await http.get(
        "https://clinicaltrials.gov/api/v2/studies"
        f"?query.titles={quote(chr(34) + phrase + chr(34))}&filter.advanced={quote(window)}"
        f"&pageSize={PER_SOURCE}"
        "&sort=StartDate:desc"
        "&fields=NCTId,BriefTitle,StartDate,Phase,LeadSponsorName,OverallStatus"
    )
    r.raise_for_status()
    return parse_trials(r.json())


def _yc_path() -> Path:
    # `resolved_data_dir`, а не `data_dir`: относительный `DATA_DIR=data` иначе ложится
    # относительно текущего каталога процесса, а не репозитория (PEL-H-09).
    return settings.resolved_data_dir / "yc-companies.json"


async def _yc_directory(http: httpx.AsyncClient) -> list[dict]:
    path = _yc_path()
    if path.exists() and time.time() - path.stat().st_mtime < YC_MAX_AGE_S:
        return json.loads(path.read_text(encoding="utf-8"))
    r = await http.get(YC_URL)
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text, encoding="utf-8")
    return r.json()


async def _one(http, source: str, phrase: str, fetch) -> tuple[str, list[LiveDoc] | None]:
    try:
        return source, await _cached(source, phrase, lambda: fetch(http, phrase))
    except (httpx.HTTPError, ValueError):
        return source, None


async def _gather(phrases: list[str]) -> list[Extra]:
    async with httpx.AsyncClient(
        timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": settings.user_agent}
    ) as http:
        try:
            directory: list[dict] | None = await _yc_directory(http)
        except (httpx.HTTPError, ValueError, OSError):
            directory = None
        gate = asyncio.Semaphore(4)

        async def per(phrase: str) -> Extra:
            async with gate:
                got = await asyncio.gather(
                    _one(http, NIH, phrase, _fetch_nih),
                    _one(http, PREPRINT, phrase, _fetch_preprints),
                    _one(http, TRIALS, phrase, _fetch_trials),
                )
            docs: dict[str, list[LiveDoc] | None] = dict(got)
            docs[YC] = None if directory is None else search_yc(directory, phrase, asof.today())
            return Extra(docs)

        return list(await asyncio.gather(*(per(p) for p in phrases)))


def gather(phrases: list[str]) -> list[Extra]:
    """По каждой фразе (ядро или ярлык кандидата) — все дополнительные источники разом."""
    return asyncio.run(_gather(phrases)) if phrases else []
