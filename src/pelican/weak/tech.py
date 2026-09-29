"""Стадия `tech`: назвать приём, который работа предлагает или использует.

Единица выдачи этой ветки — **технология**, и называет её модель из аннотации,
ровно как `mine` называет работу из заголовка. n-грамма на этом же корпусе
замерена и отвергнута: сигнал есть, но неотделим от грамматики и от осколков
одного приёма на всём диапазоне частот ([todo](../../docs/todo.md) §43).

## Роль работы — вторая половина ответа, и она не украшение

`proposes` против `uses` — это две РАЗНЫЕ оси у Yoon (2012):

- `proposes` — работа вводит приём. Счёт таких работ это **новизна**;
- `uses` — работа приём применяет. Счёт таких работ это **диффузия** (DoD),
  то есть «вышло за пределы лаборатории-автора»;
- `reviews` — обзор. ⚠️ Обзор это признак ЗРЕЛОСТИ, а не сигнала: обзоры
  пишут, когда работ уже много.

Разделение бесплатное (одно поле в той же схеме), а без него «10 работ» про
зарождающийся приём и «10 работ» про доживающий выглядят одинаково.

## ⚠️ Эхо здесь короткое, и это вынужденно

`mine` требует повторить заголовок целиком — там он короткий. Здесь на входе
`term_raw` (заголовок плюс 240 знаков аннотации), и полное эхо стоило бы дороже
самого ответа. Поэтому эхо — **первые шесть слов входа**: оно ловит сдвиг
раскладки (ради чего эхо и заводилось), а платится за него десяток токенов.

⚠️ Эхо остаётся ДИАГНОСТИКОЙ, а не ключом: раскладка позиционная, и пакет с
разошедшимся эхом не отвергается — считается счётчик, как `echo_drift` в `mine`.

⚠️ **Сравнивается эхо ПРЕФИКСОМ, а не посимвольно** — замер, см. `same_head`:
строгое сравнение объявило расхождением 37.5% выборки, и все до одной оказались
ответом ровно на слово короче запрошенного, без единого сдвига раскладки.

## ⚠️ Имя системы — не имя приёма

Главная ловушка этого корпуса: работы называют свои системы (`GPT-Red`,
`Lo-MARVE`, `MemSentry`), и модель охотно возвращает имя вместо приёма. Имя
системы не сводится с вариантами написания и не считается по корпусу — оно
уникально по построению. Поэтому промпт требует приём, а `clean_tech` отсеивает
однословные ответы с заглавными и цифрами.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from pelican.config import settings
from pelican.store import Store
from pelican.llm import Breaker, LLMClient, LLMError
from pelican.weak.core import significant

PROPOSES = "proposes"
USES = "uses"
REVIEWS = "reviews"
NONE = "none"
ROLES = (PROPOSES, USES, REVIEWS, NONE)

#: Слов в названии приёма. Ниже двух — это категория («ИИ», «роботы»), выше
#: восьми — пересказ аннотации. ⚠️ Границы НЕ замерены, догадка (todo §60).
MIN_TECH_TOKENS = 2
MAX_TECH_TOKENS = 8

#: Слов эха. Шести хватает, чтобы отличить сдвиг раскладки от вольного пересказа.
ECHO_WORDS = 6

#: Пакет. ⚠️ Меньше, чем у `mine` (15): там на входе заголовок, здесь — заголовок
#: с куском аннотации, и пакет упирается в контекст, а не в скорость.
BATCH_SIZE = 8
TOKENS_PER_ITEM = 56
TOKENS_OVERHEAD = 160

_SPACE = re.compile(r"\s+")

#: Категории вместо приёмов: то, что модель возвращает, когда приёма в работе нет.
_TOO_BROAD = {
    "machine learning",
    "deep learning",
    "artificial intelligence",
    "neural networks",
    "large language models",
    "natural language processing",
    "computer vision",
    "reinforcement learning",
    "data science",
    "blockchain",
    "internet of things",
    "cloud computing",
    "edge computing",
    "robotics",
    "optimization",
}

SYSTEM_PROMPT = """Each INPUT is the title of a scientific paper followed by the
beginning of its abstract.

Name the TECHNIQUE the paper introduces or applies — the thing that could be
adopted by someone else.

head — the first six words of the INPUT, echoed back verbatim.

tech — the technique, as a noun phrase of 2 to 8 words, in the wording a
  researcher would use in a paper title. Lowercase unless it is a proper noun.
  Name the METHOD, not the system the authors branded: for "GPT-Red: Automated
  Red Teaming via Self-Play" answer "automated red teaming via self-play", never
  "GPT-Red". Name what is specific: "speculative decoding with flash offloading",
  never "deep learning"; "electrohydraulic artificial muscles", never "robotics".
  A bare field name (machine learning, blockchain, computer vision, optimization)
  is NOT a technique — it is a category, and it is wrong here.
  Return an empty string when the paper introduces no technique at all: a policy
  or position paper, a dataset description, an editorial, a purely empirical
  measurement with no method.

role — what the paper does with that technique:
    "proposes" the paper introduces it, or a new variant of it
    "uses"     the paper applies an existing technique to a problem
    "reviews"  a survey, review, roadmap or benchmark comparison of others' work
    "none"     only when tech is empty

Answer for every INPUT, in the given order."""


@dataclass(slots=True)
class Pending:
    signal_id: int
    text: str

    @property
    def echo(self) -> str:
        return " ".join(self.text.split()[:ECHO_WORDS])


@dataclass(slots=True)
class Named:
    signal_id: int
    text: str
    tech: str
    role: str


@dataclass(slots=True)
class TechStats:
    pending: int = 0
    processed: int = 0
    named: int = 0
    empty: int = 0
    by_role: dict[str, int] = field(default_factory=dict)
    rejected: dict[str, int] = field(default_factory=dict)
    echo_drift: int = 0
    echo_swap: int = 0
    failed_batches: int = 0
    unreachable: bool = False

    @property
    def hit_rate(self) -> float:
        return self.named / self.processed if self.processed else 0.0


def batch_schema(n: int) -> dict[str, Any]:
    """Пустой приём — валидный ответ, поэтому minLength у tech нет."""
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": n,
                "maxItems": n,
                "items": {
                    "type": "object",
                    "properties": {
                        "head": {"type": "string"},
                        "tech": {"type": "string"},
                        "role": {"type": "string", "enum": list(ROLES)},
                    },
                    "required": ["head", "tech", "role"],
                },
            }
        },
        "required": ["items"],
    }


def batch_token_budget(batch: list[Pending]) -> int:
    echo = sum(len(json.dumps(item.echo)) for item in batch)
    return echo + TOKENS_PER_ITEM * len(batch) + TOKENS_OVERHEAD


def build_prompt(batch: list[Pending]) -> str:
    return "\n\n".join(f"INPUT: {item.text}" for item in batch)


def clean_tech(raw: str) -> tuple[str, str]:
    """(приём, причина отказа). Пустая причина — приём принят."""
    tech = _SPACE.sub(" ", raw).strip().strip('"').strip()
    if not tech:
        return "", ""
    tokens = tech.split()
    if len(tokens) < MIN_TECH_TOKENS:
        # ⚠️ Однословный ответ почти всегда имя системы («MemSentry») или
        # категория («robotics»); и то и другое по корпусу не считается.
        return "", "одно слово: имя системы или категория"
    if len(tokens) > MAX_TECH_TOKENS:
        return "", "пересказ аннотации"
    if tech.lower() in _TOO_BROAD:
        return "", "категория, а не приём"
    return tech, ""


#: Слова рынка: они называют не механизм, а полку в магазине. ⚠️ Список — догадка,
#: но проверяемая: см. правило ниже и замер долей в `scripts/measure_ask.py`.
MARKET_WORDS = frozenset(
    {
        "enterprise", "software", "platform", "platforms", "solution", "solutions",
        "service", "services", "system", "systems", "technology", "technologies",
        "tool", "tools", "protection", "threat", "threats", "intelligence",
        "automation", "orchestration", "scaling", "stack", "suite", "infrastructure",
        "application", "applications", "operations", "management", "monitoring",
        "advanced", "autonomous", "smart", "modern", "digital", "cloud",
        # Эхо эпохи: «agent» стоит в половине заголовков 2026 года и один, без
        # механизма рядом, направления не называет.
        "agent", "agents", "multi", "next", "generation", "based", "driven",
        "powered", "native",
    }
)


def _parts(words: list[str]) -> list[str]:
    """Слова с раскрытыми дефисами: «multi-agent» — это «multi» и «agent»."""
    out: list[str] = []
    for w in words:
        out.extend(p for p in w.replace("/", "-").split("-") if len(p) > 2)
    return out


def is_category(label: str, query_words: set[str]) -> bool:
    """Название пусто по содержанию: в нём нет НИ ОДНОГО слова сверх запроса и рынка.

    Приём — тот же, что у `clean_tech._TOO_BROAD`, но относительно запроса: «enterprise
    ai software security» на запрос про безопасность ИИ не добавляет к запросу ничего,
    а «security scanners for MCP servers» добавляет «scanners» и «MCP».

    ⚠️ Это правило существует потому, что требование coherence премирует расплывчатость:
    чем обобщённее формулировка, тем больше компаний под неё подходит, и модель,
    которую просят назвать общее для нескольких компаний, съезжает в название рынка.

    Замер на 19 наблюдённых названиях ТОП и 8 строках заказчика: снято 6 зонтиков из 6,
    ложных срабатываний на строках заказчика — 0 (docs/weak-signals.md).
    """
    rest = [
        w
        for w in _parts(significant(label))
        if w not in query_words and w not in MARKET_WORDS
    ]
    return not rest


def query_words(*texts: str) -> set[str]:
    """Слова самого запроса: его перевод, область и фасеты — всё, что спрашивали."""
    words: set[str] = set()
    for text in texts:
        low = (text or "").lower()
        words |= set(significant(low)) | {w for w in low.split() if len(w) > 2}
    return words | set(_parts(sorted(words)))


#: Ниже этой длины эхо ничего не доказывает: три слова совпадут у половины
#: работ корпуса, и «совпало» перестанет отличать свой вход от чужого.
MIN_ECHO_WORDS = 3


def _norm(text: str) -> str:
    return _SPACE.sub(" ", text).strip().lower()


def same_head(sent: str, echoed: str) -> bool:
    """Про этот ли вход ответ. Сравнение ПРЕФИКСНОЕ, а не посимвольное.

    ⚠️ Точное сравнение здесь замерено и не годится: на выборке в 200 работ оно
    дало 37.5% «расхождений», и все до одной оказались тем, что модель вернула
    ровно на слово меньше запрошенных шести («Assessment of building
    vulnerability to» против «...to tsunamis:»). Раскладка при этом не съезжала
    ни разу. Строгая проверка мерила длину ответа, а не его адресность.

    Поэтому проверяется то, ради чего эхо заведено: является ли ответ началом
    СВОЕГО входа. Длина ответа модели при этом её личное дело.
    """
    a, b = _norm(sent), _norm(echoed)
    if len(b.split()) < MIN_ECHO_WORDS:
        return False
    return a.startswith(b) or b.startswith(a)


def parse_batch(payload: dict[str, Any], batch: list[Pending], stats: TechStats) -> list[Named]:
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != len(batch):
        # Раскладка позиционная: сдвиг на элемент припишет приём чужой работе.
        # Пакет отвергается целиком — очередь его вернёт.
        raise ValueError(f"ожидали {len(batch)} элементов, пришло {len(items or [])}")

    out: list[Named] = []
    for source, got in zip(batch, items, strict=True):
        echoed = str(got.get("head", ""))
        if not same_head(source.echo, echoed):
            stats.echo_drift += 1
            # ⚠️ Совпадение с ЧУЖИМ входом того же пакета — это улика СДВИГА
            # РАСКЛАДКИ, а не вольного пересказа, и различать их обязательно:
            # первое приписывает приём чужой работе, второе безобидно. Тот же
            # счётчик и по той же причине заведён в `mine` (`echo_swap`).
            if any(other is not source and same_head(other.echo, echoed) for other in batch):
                stats.echo_swap += 1
        role = str(got.get("role") or NONE)
        tech, reason = clean_tech(str(got.get("tech") or ""))

        if role not in (PROPOSES, USES, REVIEWS) or not tech:
            if reason:
                stats.rejected[reason] = stats.rejected.get(reason, 0) + 1
            elif tech and role == NONE:
                stats.rejected["приём без роли"] = stats.rejected.get("приём без роли", 0) + 1
            else:
                stats.empty += 1
            out.append(Named(source.signal_id, source.text, "", NONE))
            continue

        stats.named += 1
        stats.by_role[role] = stats.by_role.get(role, 0) + 1
        out.append(Named(source.signal_id, source.text, tech, role))
    return out


#: Горизонт очереди. ⚠️ Свой, а не `text.QUEUE_RETRY_DAYS` (180 дней), и это не
#: описка: у болей полгода — граница, за которой «этим болеют сейчас» уже
#: неправда, а приём живёт годами, и ряд под ось роста нужен длинный. Верхняя
#: граница здесь — покрытие векторов (`emb-science/state.json`, 2023-07-27):
#: глубже привязка всё равно не работает.
QUEUE_DAYS = 760

ProgressHook = Callable[[int, Exception | None], None] | None


async def _tech_batch(
    client: LLMClient, http: httpx.AsyncClient, batch: list[Pending], stats: TechStats
) -> list[Named]:
    payload = await client.json_completion(
        http,
        system=SYSTEM_PROMPT,
        user=build_prompt(batch),
        schema=batch_schema(len(batch)),
        name="techs",
        max_tokens=batch_token_budget(batch),
    )
    return parse_batch(payload, batch, stats)


async def _worker(
    queue: asyncio.Queue[list[Pending]],
    client: LLMClient,
    http: httpx.AsyncClient,
    store: Store,
    results: list[Named],
    stats: TechStats,
    breaker: Breaker,
    on_batch: ProgressHook = None,
) -> None:
    while not breaker.tripped:
        try:
            batch = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            named = await _tech_batch(client, http, batch, stats)
        except (LLMError, httpx.HTTPError, ValueError, KeyError) as exc:
            stats.failed_batches += 1
            if on_batch:
                on_batch(len(batch), exc)
            if breaker.record(exc):
                # Машины с моделью нет: остаток очереди ответит тем же, а стадия
                # инкрементальна и дождётся следующего запуска планировщика.
                stats.unreachable = True
                return
            continue
        breaker.record(None)

        # Пишем сразу: прерванный прогон не теряет оплаченное GPU-время.
        store.save_tech(client.model, [(n.signal_id, n.tech, n.role) for n in named])
        stats.processed += len(named)
        results.extend(n for n in named if n.tech)
        if on_batch:
            on_batch(len(batch), None)


def run_tech(
    store: Store,
    limit: int | None = None,
    batch_size: int | None = None,
    on_batch: ProgressHook = None,
    on_start: Callable[[int], None] | None = None,
    ids: Sequence[int] | None = None,
) -> tuple[list[Named], TechStats]:
    """Дренировать очередь аннотаций, записывая приём и роль работы.

    `ids` — только эти работы (найденное поиском), а не вся очередь.
    """
    return asyncio.run(_run(store, limit, batch_size, on_batch, on_start, ids))


async def _run(
    store: Store,
    limit: int | None,
    batch_size: int | None,
    on_batch: ProgressHook,
    on_start: Callable[[int], None] | None,
    ids: Sequence[int] | None = None,
) -> tuple[list[Named], TechStats]:
    stats = TechStats()
    model = settings.llm_model
    pending = [
        Pending(signal_id=sid, text=text)
        for sid, text in store.untagged_science(
            model,
            since=datetime.now(UTC) - timedelta(days=QUEUE_DAYS),
            limit=limit,
            ids=ids,
        )
    ]
    stats.pending = len(pending)
    if on_start:
        on_start(len(pending))
    if not pending:
        return [], stats

    size = batch_size or BATCH_SIZE
    client = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=model,
        timeout_s=settings.llm_timeout_s,
        reasoning_effort=settings.llm_reasoning_effort,
    )

    # Конвейеризация, а не concurrency: LM Studio выполняет запросы по одному, и
    # `llm_pipeline_depth` лишь держит её очередь непустой (docs/llm-setup.md).
    batches = [pending[i : i + size] for i in range(0, len(pending), size)]
    queue: asyncio.Queue[list[Pending]] = asyncio.Queue()
    for batch in batches:
        queue.put_nowait(batch)

    out: list[Named] = []
    # Предохранитель один на стадию: порознь по воркерам порог набирался бы втрое дольше.
    breaker = Breaker()
    async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as http:
        await asyncio.gather(
            *(
                _worker(queue, client, http, store, out, stats, breaker, on_batch)
                for _ in range(min(settings.llm_pipeline_depth, len(batches)))
            )
        )
    return out, stats
