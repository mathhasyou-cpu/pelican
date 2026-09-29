"""Денежный поток кандидатов: направления, за которыми стоят НЕСКОЛЬКО компаний.

## Зачем второй поток, а не только корпус

Первые прогоны `trends ask` по кибербезопасности вывели в ТОП federated learning,
RAG, deep RL и графовые сети — прикладное академическое ML. Причина не в пороге, а в
источнике кандидатов: научный корпус отвечает «что исследуют», а заказчик размечал
слабые сигналы по деньгам — 105 упоминаний раунда/суммы против 23 «мало публикаций»
в обоснованиях датасета (docs/weak-signals.md).

## Единица — направление, а не заголовок

⚠️ **Заголовок по одному не даёт единицы выдачи.** Замер: «AI agent identity startup
Keycard acquires Anchor.dev» при пошаговом именовании превращалось в «ai security
platform», а имя компании выбрасывалось вовсе. В датасете заказчика у строки **медиана
4 названные компании**, и слабый сигнал у него ровно там, где несколько мелких игроков
делают одно и то же — это **coherence** у Rotolo и др. (2015) и **degree of diffusion**
у Yoon (2012). Увидеть общее можно только на ПАЧКЕ заголовков, поэтому модель получает
15 штук разом и группирует их, называя и направление, и компании.

⚠️ **Две проверки против выдумки**, обе дешёвые и обе обязательны:

- направление обязано ссылаться на номера заголовков из своей же пачки — номер вне
  диапазона означает, что модель сочиняет (тот же приём, что `tech.same_head`);
- имя компании обязано встречаться в заголовке **с тем же регистром**. Регистр здесь
  работает: компании в заголовках с заглавной, а «ai», «security», «platform» — нет.

⚠️ **Формулы запросов — ДОГАДКА** (docs/todo.md §65): взяты по словарю обоснований
датасета («раунд», «вышли из stealth», «первые пилоты»), а не подобраны замером. Узкие
формулировки работают лучше голого названия области: «AI agent identity startup» отдаёт
Keycard, NewCore и Hush Security, а «cybersecurity startup raises» — общие категории.
Поэтому `queries()` смешивает шаблоны по области с фасетами запроса.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from pelican.config import settings
from pelican.llm import LLMClient
from pelican.weak.live import LiveDoc, news_many
from pelican.weak.tech import clean_tech

#: ⚠️ ДОГАДКА: формулы по словарю обоснований датасета, не по замеру.
TEMPLATES = (
    "{q} startup raises",
    "{q} seed funding",
    "{q} series A",
    "{q} emerges from stealth",
    "{q} pilot deployment",
)
#: Приставки к фасетам запроса. ⚠️ Догадка: короткие, чтобы не сузить поиск до нуля.
#: ⚠️ Их два, а не три, ОСОЗНАННО: бюджет запросов отдан второму кругу по сущностям
#: (`follow_up`), потому что широкий запрос отдаёт рынок, а запрос по артефакту — тех,
#: кто артефакт делает.
FACET_TEMPLATES = ("{q} startup", "{q} funding")
#: Заголовков в пачке. Больше — модель начинает склеивать несвязанное, меньше — общего
#: между компаниями не видно. ⚠️ Догадка.
BATCH = 15
#: Заголовков в пачке на извлечение имён компаний.
NAMES_BATCH = 20
#: Сколько компаний держим у кандидата: у заказчика медиана 4 на строку.
MAX_COMPANIES = 8

GROUP_PROMPT = """Each HEADLINE is an industry news title about a company, a funding
round, a product launch or a pilot.

Group the headlines by the TECHNOLOGY DIRECTION the companies are building. Return one
item per direction:
  tech      - the direction, as a noun phrase of 2 to 8 words in English, the way an
              analyst would name the category ("identity and access management for AI
              agents", "security scanners for MCP servers", "analog compute-in-memory
              chips"). Never a company or product name, never a whole field
              ("cybersecurity", "artificial intelligence", "fintech").
  companies - the companies building it, spelled exactly as in the headlines. Only
              companies: never a university, research institute, journal, conference,
              funding programme or a person's name.
  headlines - the numbers of the headlines that belong to this direction.

Prefer directions that SEVERAL different companies share - that is what makes a
direction worth naming. Leave out a headline that says nothing about the technology.

Name the MECHANISM, the artifact, the protocol or the standard - never the market.
A name built only of market words is useless: it fits every company at once.
  bad:  "enterprise AI software security", "AI security and threat protection",
        "enterprise AI agent orchestration", "multi-agent systems for finance"
  good: "identity and access management for AI agents", "security scanners for MCP
        servers", "purpose-bound programmable money", "machine unlearning for model
        risk remediation"
If two directions differ only in wording, return them as ONE direction.

Also return `terms`: the SPECIFIC technical things these headlines name. Any kind:
  - software: a protocol, standard, file format, attack, regulation, benchmark
    ("MCP", "HTTP 402", "SBOM", "prompt injection", "EU AI Act", "LoRA");
  - hardware: a component, device, material, sensor, actuator, chip architecture
    ("dexterous hand", "HASEL actuator", "tactile sensor", "microrobot", "RISC-V");
  - method: a named technique or data source ("teleoperation data", "sim-to-real").
Spell each exactly as in the headline. Never a company, a product or a general word
("AI", "platform", "security", "startup"). Return an empty list if there are none.
When THE USER ASKED ABOUT is given, `terms` hold only things of that subject: a
headline from another field gives no terms."""

NAMES_PROMPT = """Each HEADLINE is an industry news title.

For each headline return:
  n         - its number
  companies - the companies or startups named in it, spelled exactly as in the
              headline. Only companies: never a university, research institute, journal,
              conference, funding programme or a person's name. Empty list when the
              headline names none."""


@dataclass(frozen=True, slots=True)
class MoneyCandidate:
    label: str
    headlines: tuple[LiveDoc, ...]
    companies: tuple[str, ...] = ()


def queries(area_en: str, facets_en: list[str], submarkets_en: Sequence[str] = ()) -> list[str]:
    """Новостные запросы первого круга: шаблоны по области, фасеты, подрынки.

    Подрынки (компоненты, услуги, сертификация, страхование — `ask.expand`) идут теми же
    короткими приставками, что и фасеты: второй круг по сущностям (`follow_up`) углубляет
    найденное первым, и без подрынков в первом круге железо, услуги и страхование
    области не находятся вовсе (docs/todo.md §76). Бюджет: 5 + 2·|фасеты| + 2·|подрынки|.
    """
    out = [tpl.format(q=area_en) for tpl in TEMPLATES]
    out += [tpl.format(q=f) for f in facets_en for tpl in FACET_TEMPLATES]
    out += [tpl.format(q=f) for f in submarkets_en for tpl in FACET_TEMPLATES]
    seen: set[str] = set()
    return [q for q in out if not (q.lower() in seen or seen.add(q.lower()))]


def _group_schema(n: int) -> dict:
    item = {
        "type": "object",
        "properties": {
            "tech": {"type": "string"},
            "companies": {"type": "array", "items": {"type": "string"}},
            "headlines": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["tech", "companies", "headlines"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": item, "maxItems": n},
            "terms": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["items", "terms"],
        "additionalProperties": False,
    }


def _names_schema(n: int) -> dict:
    item = {
        "type": "object",
        "properties": {
            "n": {"type": "integer"},
            "companies": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["n", "companies"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": item, "minItems": n, "maxItems": n}},
        "required": ["items"],
        "additionalProperties": False,
    }


#: Слова академического мира: «Scientific Reports», «College of Engineering», «AAAI-26»
#: проверку регистром проходят, компаниями не являются и в прогоне по безопасности ИИ
#: протащили в ТОП три научных кандидата с «игроками»-журналами. ⚠️ Список — догадка.
_NOT_A_COMPANY_WORD = (
    "universit",
    "institute",
    "college",
    "school",
    "laborator",
    "journal",
    "conference",
    "symposium",
    "workshop",
    "proceedings",
    "foundation",
    "academy",
    "faculty",
    "department",
    "scientific reports",
    "nature ",
    "ieee",
    "acm ",
    "aaai",
    "neurips",
    "arxiv",
    "springer",
    "elsevier",
    # Аналитики и деловые медиа: они пишут о направлении, а не строят его.
    "gartner",
    "forrester",
    "idc",
    "mckinsey",
    "deloitte",
    "techcrunch",
    "reuters",
    "bloomberg",
    "forbes",
    "venturebeat",
    "siliconangle",
    "crunchbase",
)
#: Аббревиатуры, которые модель иногда выдаёт за компанию: пишутся с заглавных и
#: стоят в заголовке, то есть проверку регистром проходят. ⚠️ Список — догадка.
_NOT_COMPANY = frozenset(
    {
        "ai",
        "ml",
        "llm",
        "llms",
        "mcp",
        "api",
        "saas",
        "iot",
        "ot",
        "it",
        "eu",
        "us",
        "uk",
        "ai agent",
        "ai agents",
        "gen ai",
        "genai",
    }
)


#: Слова, с которых начинается предложение, а не имя компании. ⚠️ Проверка регистром
#: их пропускает: «This startup raises $20M» даёт «игрока» по имени «This startup».
_NOT_A_NAME_START = frozenset(
    {
        "this", "that", "these", "those", "the", "a", "an", "new", "two", "three",
        "first", "top", "best", "why", "how", "what", "when", "meet", "inside",
        "his", "her", "their", "our", "its", "and", "but", "after", "before",
    }
)


def named_in(text: str, candidates: list[str]) -> list[str]:
    """Имена, которые ДЕЙСТВИТЕЛЬНО стоят в тексте — сравнение с учётом регистра.

    ⚠️ Регистр здесь и есть фильтр: компания в заголовке пишется с заглавной, а «ai»,
    «security», «platform» — нет, и без этого правила в компании попадали слова темы.
    """
    out: list[str] = []
    for raw in candidates:
        name = " ".join(str(raw or "").split()).strip(".,;:")
        if len(name) < 2 or not name[0].isupper() or name not in text:
            continue
        low = name.lower()
        if low in _NOT_COMPANY or any(w in low for w in _NOT_A_COMPANY_WORD):
            continue
        if low.split()[0] in _NOT_A_NAME_START:
            continue
        if name.lower() not in {x.lower() for x in out}:
            out.append(name)
    return out


def headlines(queries_en: list[str]) -> list[LiveDoc]:
    """Заголовки по всем запросам, без повторов по заголовку."""
    found = news_many(queries_en, "en")
    seen: set[str] = set()
    out: list[LiveDoc] = []
    for docs in found:
        for d in docs:
            key = d.title.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(d)
    return out


def _client() -> LLMClient:
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )


def parse_group(payload: dict, chunk: list[LiveDoc]) -> list[MoneyCandidate]:
    """Разбор одной пачки с обеими проверками: номера в диапазоне, имена — в тексте."""
    out: list[MoneyCandidate] = []
    for item in payload.get("items", []):
        tech, _reason = clean_tech(str(item.get("tech") or ""))
        raw_numbers = item.get("headlines", [])
        numbers = sorted(
            {int(n) for n in raw_numbers if isinstance(n, int) and 1 <= n <= len(chunk)}
        )
        if not tech or not numbers:
            # Направление без единого своего заголовка неотличимо от знаний модели.
            continue
        docs = [chunk[n - 1] for n in numbers]
        text = " ".join(d.title for d in docs)
        companies = named_in(text, [str(c) for c in item.get("companies", [])])
        out.append(MoneyCandidate(tech.lower(), tuple(docs), tuple(companies[:MAX_COMPANIES])))
    return out


#: Сколько сущностей уходит во второй круг запросов. ⚠️ Догадка, зажатая бюджетом:
#: каждая стоит одного новостного запроса.
FOLLOW_TERMS = 12
#: Запросы второго круга по найденной сущности. ⚠️ Догадка той же природы, что TEMPLATES.
FOLLOW_TEMPLATES = ("{q} startup", "{q} funding")
#: То, что модель зовёт «сущностью», а это тема запроса, а не артефакт.
_NOT_A_TERM = frozenset(
    {
        "ai", "ml", "llm", "llms", "genai", "gen ai", "agent", "agents", "ai agent",
        "ai agents", "security", "cybersecurity", "platform", "startup", "funding",
        "cloud", "edge", "robot", "robots", "robotics", "fintech", "data", "model",
        "models", "software", "hardware", "chip", "chips", "inference", "training",
    }
)


def parse_terms(payload: dict, chunk: list[LiveDoc]) -> list[str]:
    """Сущности, КОТОРЫЕ ДЕЙСТВИТЕЛЬНО СТОЯТ в заголовках пачки.

    Приём — **обратная связь по псевдорелевантности** (Rocchio 1971, RM3): верх выдачи
    первого круга даёт слова для второго круга запросов. ⚠️ Нужен он потому, что широкий
    запрос отдаёт широкие названия: «AI security startup» — это рынок, а строка заказчика
    «сканеры безопасности MCP-серверов» находится запросом по самому артефакту.

    ⚠️ Проверка та же, что у компаний: сущность обязана встречаться в тексте пачки, иначе
    это знание модели, а не то, что мы прочитали.
    """
    text = " ".join(d.title for d in chunk).lower()
    out: list[str] = []
    for raw in payload.get("terms", []):
        term = " ".join(str(raw or "").split()).strip(".,;:")
        low = term.lower()
        if not (2 <= len(term) <= 40) or low in _NOT_A_TERM or low not in text:
            continue
        if low not in {x.lower() for x in out}:
            out.append(term)
    return out


def follow_up(terms: list[str], asked: set[str]) -> list[str]:
    """Запросы второго круга: по сущности, а не по области. Заданные не повторяются."""
    out: list[str] = []
    for term in terms[:FOLLOW_TERMS]:
        for tpl in FOLLOW_TEMPLATES:
            q = tpl.format(q=term)
            if q.lower() not in asked:
                asked.add(q.lower())
                out.append(q)
    return out


async def _group(docs: list[LiveDoc], area: str = "") -> tuple[list[MoneyCandidate], list[str]]:
    client = _client()
    head = f"THE USER ASKED ABOUT: {area}\n\n" if area else ""
    out: list[MoneyCandidate] = []
    terms: list[str] = []
    async with httpx.AsyncClient() as http:
        for start in range(0, len(docs), BATCH):
            chunk = docs[start : start + BATCH]
            got = await client.json_completion(
                http,
                system=GROUP_PROMPT,
                user=head + "\n".join(f"{i + 1}. {d.title}" for i, d in enumerate(chunk)),
                schema=_group_schema(len(chunk)),
                name="money_directions",
                max_tokens=110 * len(chunk) + 300,
            )
            out.extend(parse_group(got, chunk))
            for term in parse_terms(got, chunk):
                if term.lower() not in {x.lower() for x in terms}:
                    terms.append(term)
    return out, terms


async def _companies(docs: list[LiveDoc]) -> list[tuple[str, ...]]:
    client = _client()
    out: list[tuple[str, ...]] = []
    async with httpx.AsyncClient() as http:
        for start in range(0, len(docs), NAMES_BATCH):
            chunk = docs[start : start + NAMES_BATCH]
            found: list[tuple[str, ...]] = [()] * len(chunk)
            got = await client.json_completion(
                http,
                system=NAMES_PROMPT,
                user="\n".join(f"{i + 1}. {d.title}" for i, d in enumerate(chunk)),
                schema=_names_schema(len(chunk)),
                name="headline_companies",
                max_tokens=40 * len(chunk) + 200,
            )
            for item in got.get("items", []):
                k = int(item.get("n") or 0)
                if 1 <= k <= len(chunk):
                    raw = [str(c) for c in item.get("companies", [])]
                    names = named_in(chunk[k - 1].title, raw)
                    found[k - 1] = tuple(names)
            out.extend(found)
    return out


def companies_of(docs: list[LiveDoc]) -> list[tuple[str, ...]]:
    """Компании, названные в каждом заголовке. Раскладка по номеру, а не по позиции."""
    return asyncio.run(_companies(docs)) if docs else []


def candidates(
    queries_en: list[str], on_progress: Callable[[str], None] | None = None, area: str = ""
) -> tuple[list[MoneyCandidate], int]:
    """(направления, сколько заголовков просмотрено) — ДВА круга запросов.

    Второй круг идёт по сущностям, найденным в заголовках первого (`parse_terms`):
    широкий запрос отдаёт рынок, запрос по артефакту — тех, кто этот артефакт делает.

    ⚠️ Сущности второго круга берутся только из предмета запроса (`area`): обратная
    связь по псевдорелевантности углубляет всё, что пришло в первый круг, в том числе
    чужое. Это дрейф запроса (query drift, Mitra, Singhal & Buckley, SIGIR 1998). Лечится
    он тем, что для обратной связи отбирается только относящееся к исходному запросу.
    Пример: в «art brut и prison art» подрынок «art authentication services» принёс
    заголовки про MCP, второй круг пошёл по «MCP startup», и MCP попал в ТОП.
    """

    def say(message: str) -> None:
        if on_progress:
            on_progress(message)

    docs = headlines(queries_en)
    if not docs:
        return [], 0
    found, terms = asyncio.run(_group(docs, area))
    asked = {q.lower() for q in queries_en}
    more = follow_up(terms, asked)
    if not more:
        return found, len(docs)
    say(f"второй круг: {len(terms)} найденных сущностей, {len(more)} запросов")
    seen = {d.title.strip().lower() for d in docs}
    extra = [d for d in headlines(more) if d.title.strip().lower() not in seen]
    if not extra:
        return found, len(docs)
    say(f"второй круг: ещё {len(extra)} заголовков")
    found2, _ = asyncio.run(_group(extra, area))
    known = {c.label for c in found}
    found += [c for c in found2 if c.label not in known]
    return found, len(docs) + len(extra)
