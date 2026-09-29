"""Замер привязки: находятся ли 100 названий технологий в научном корпусе.

    python scripts/measure_grounding.py --top 20
    python scripts/measure_grounding.py --arms ru,en-llm --limit 20   # быстрый прогон

## Зачем это первый замер, до всякого кода стадий

Все остальные замеры могут сдвинуть константу. Этот может отменить конструкцию.
Датасет организаторов написан ПО-РУССКИ короткими названиями технологий, а
корпус — английские заголовки с аннотациями (`term_raw` = заголовок плюс первые
240 знаков). Если названия в корпусе не находятся, то признаков у классификатора
нет вовсе, и система вырождается в «LLM отвечает из своих весов, ссылки
приделаны сбоку» — другой продукт, который и собирают иначе.

⚠️ **Корпус векторизован без query-префикса EmbeddingGemma** — намеренно, ради
сравнимости с `need_embeddings` (`docs/science-vectors.md`). Значит и запрос
идёт без префикса, иначе сравниваются разные пространства. Кросс-язычная
привязка на таких векторах здесь НЕ замерена ни разу: примеры в заметке
англо-английские.

## Плечи

| плечо | запрос |
|---|---|
| `ru` | название как написали методологи |
| `ru+domain` | название плюс «Область» — форма, которой и будет пользоваться открытый запрос |
| `en-llm` | один вызов модели переводит название в английский технический термин |
| `en-llm+expand` | модель даёт три английских синонима, вектор запроса — их среднее |

⚠️ **Нулевой контроль обязателен** (правило проекта): те же сто запросов из
переставленных слов разных строк. Если бессмыслица тоже собирает top-20, который
человек готов принять, то принимает он не то, и метрику надо ужесточать до
точного совпадения на первом месте.

## Что печатается помимо TSV

**Диагностика хабов.** Поправка на hubness (CSLS в `cluster`, local scaling в
`search_corpus`) нужна там, где корпуса разной природы, — а здесь именно такой
случай. Но вводить её вслепую нельзя, поэтому она сначала МЕРЯЕТСЯ: сколько
различных документов собрали все top-20 вместе и как часто побеждает самый
цепкий. Хабы есть — поправка обоснована замером; хабов нет — поправка не нужна.

⚠️ Приговор по TSV выносит человек: колонка `verdict` пустая, и заполняется она
глазами. Балл косинуса приговором не является — у разных тем «похоже»
начинается на разных числах (`docs/search.md`).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io as _io
import random
import sys
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Консоль планировщика и Git Bash приезжают в cp1251, а вывод здесь русский.
sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pelican.config import DATA_DIR, settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.llm import LLMClient  # noqa: E402
from pelican.embed import embed, normalise  # noqa: E402
from pelican.weak import ShardCorpus, junk_ids, top_k  # noqa: E402

TSV = Path(__file__).resolve().parent / "weak_signals_100.tsv"
EMB_DIR = DATA_DIR / "emb-science"
ARMS = ("ru", "ru+domain", "en-llm", "en-llm+expand", "null")
#: Сколько названий переводить одним вызовом. Ответ — эхо плюс перевод плюс три
#: синонима, поэтому пакет здесь меньше классификационного (LLM_BATCH_SIZE = 40).
TRANSLATE_BATCH = 10

SYSTEM = """You translate Russian names of technologies into the English terms
actually used in scientific paper titles and abstracts.

For each item return:
  ru        - the Russian name, echoed back verbatim
  en        - the single best English technical term, as a researcher would write
              it in a paper title. No explanation, no Russian, no quotes.
  synonyms  - exactly 3 alternative English phrasings of the SAME technology,
              different wordings, not broader categories.

Never translate word by word when the field has an established English term.
Answer for every item, in the given order."""


def translate_schema(n: int) -> dict:
    item = {
        "type": "object",
        "properties": {
            "ru": {"type": "string"},
            "en": {"type": "string"},
            "synonyms": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 3,
            },
        },
        "required": ["ru", "en", "synonyms"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": item, "minItems": n, "maxItems": n}},
        "required": ["items"],
        "additionalProperties": False,
    }


async def translate(names: list[str]) -> list[dict]:
    """Перевод пакетами. Раскладка позиционная, эхо — только диагностика."""
    client = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
    out: list[dict] = []
    drift = 0
    async with httpx.AsyncClient() as http:
        for start in range(0, len(names), TRANSLATE_BATCH):
            chunk = names[start : start + TRANSLATE_BATCH]
            user = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(chunk))
            got = await client.json_completion(
                http,
                system=SYSTEM,
                user=user,
                schema=translate_schema(len(chunk)),
                name="translations",
                max_tokens=120 * len(chunk) + 200,
            )
            items = got.get("items", [])
            for i, name in enumerate(chunk):
                item = items[i] if i < len(items) else {}
                if (item.get("ru") or "").strip() != name.strip():
                    drift += 1
                synonyms = [s.strip() for s in (item.get("synonyms") or []) if s.strip()]
                out.append({"en": (item.get("en") or "").strip() or name, "synonyms": synonyms})
            print(f"  переведено {len(out)}/{len(names)}", flush=True)
    if drift:
        print(f"  [!] эхо разошлось на {drift} строках — раскладка позиционная, смотреть глазами")
    return out


def shuffled_names(names: list[str], seed: int = 20260915) -> list[str]:
    """Нулевой контроль: слова настоящих названий, скрещенные между строками.

    Не случайные буквы и не чужой язык — ровно та же лексика и та же длина,
    но без смысла. Иначе контроль проверял бы не то.
    """
    rng = random.Random(seed)
    pools = [n.split() for n in names]
    fake = []
    for words in pools:
        picked = [rng.choice(pools[rng.randrange(len(pools))]) for _ in words]
        fake.append(" ".join(picked))
    return fake


def build_queries(
    rows: list[dict], arms: list[str], translations: list[dict] | None
) -> list[tuple[str, int, list[str]]]:
    """[(плечо, номер строки, тексты запроса)]."""
    plan: list[tuple[str, int, list[str]]] = []
    fakes = shuffled_names([r["tech"] for r in rows]) if "null" in arms else []
    for i, row in enumerate(rows):
        if "ru" in arms:
            plan.append(("ru", i, [row["tech"]]))
        if "ru+domain" in arms:
            plan.append(("ru+domain", i, [row["tech"] + ". " + row["domain"]]))
        if "en-llm" in arms and translations:
            plan.append(("en-llm", i, [translations[i]["en"]]))
        if "en-llm+expand" in arms and translations:
            plan.append(("en-llm+expand", i, [translations[i]["en"], *translations[i]["synonyms"]]))
        if "null" in arms:
            plan.append(("null", i, [fakes[i]]))
    return plan


def embed_plan(plan: list[tuple[str, int, list[str]]]) -> np.ndarray:
    """Один вектор на запрос. У `expand` — среднее по синонимам, снова нормированное.

    Приём — **query expansion усреднением векторов** (centroid of the expanded
    query, Rocchio без отрицательной части): дешевле, чем k прогонов поиска, и
    на нормированных векторах среднее остаётся в том же пространстве.
    """
    flat: list[str] = []
    spans: list[tuple[int, int]] = []
    for _, _, texts in plan:
        spans.append((len(flat), len(flat) + len(texts)))
        flat.extend(texts)
    print(f"эмбеддинг {len(flat)} текстов ({len(plan)} запросов)...", flush=True)
    with httpx.Client(timeout=httpx.Timeout(settings.llm_timeout_s)) as http:
        vectors = embed(http, flat, settings.embedding_model, settings.embedding_batch_size)
    matrix = np.asarray(vectors, dtype=np.float32)
    out = np.empty((len(plan), matrix.shape[1]), dtype=np.float32)
    for i, (lo, hi) in enumerate(spans):
        out[i] = np.asarray(normalise(matrix[lo:hi].mean(axis=0)), dtype=np.float32)
    return out


def resolve(store: Store, ids: list[int]) -> dict[int, tuple]:
    """id -> (источник, дата, заголовок, ссылка). Кусками: литералы едут в SQL текстом."""
    found: dict[int, tuple] = {}
    uniq = sorted({int(i) for i in ids if int(i) >= 0})
    for start in range(0, len(uniq), 1000):
        listed = ",".join(str(i) for i in uniq[start : start + 1000])
        sql = (
            "SELECT id, source, CAST(observed_at AS DATE), term_raw, url "
            f"FROM works WHERE id IN ({listed})"
        )
        for row in store.conn.execute(sql).fetchall():
            found[int(row[0])] = (row[1], str(row[2]), row[3], row[4])
    return found


def report(plan, ids, scores, arms) -> None:
    print("\n--- балл первого места по плечам (медиана / минимум / максимум) ---")
    for arm in arms:
        idx = [i for i, (a, _, _) in enumerate(plan) if a == arm]
        if not idx:
            continue
        first = scores[idx, 0]
        print(
            f"  {arm:16s} med {np.median(first):.3f}   "
            f"min {first.min():.3f}   max {first.max():.3f}"
        )

    print("\n--- диагностика хабов: один и тот же документ на много запросов ---")
    for arm in arms:
        idx = [i for i, (a, _, _) in enumerate(plan) if a == arm]
        if not idx:
            continue
        flat = ids[idx].ravel()
        flat = flat[flat >= 0]
        uniq, counts = np.unique(flat, return_counts=True)
        share = len(uniq) / len(flat) if len(flat) else 0.0
        top = int(counts.max()) if len(counts) else 0
        print(
            f"  {arm:16s} различных документов {len(uniq)} из {len(flat)} ({share:.0%}); "
            f"самый цепкий взят {top} раз(а) при {len(idx)} запросах"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="замер привязки названий к научному корпусу")
    ap.add_argument("--tsv", type=Path, default=TSV)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--limit", type=int, default=0, help="взять первые N названий (для отладки)")
    ap.add_argument("--out", type=Path, default=Path("reports/grounding.tsv"))
    ap.add_argument(
        "--exclude-junk",
        action="store_true",
        help="не пускать в выдачу строки без содержания (weak.corpus: словарные статьи, "
        "датасеты, заголовки короче 60 знаков)",
    )
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = set(arms) - set(ARMS)
    if bad:
        raise SystemExit(f"неизвестные плечи: {sorted(bad)}; доступны {list(ARMS)}")

    rows = list(csv.DictReader(args.tsv.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    if args.limit:
        rows = rows[: args.limit]
    print(f"названий: {len(rows)}, плечи: {arms}")

    translations = None
    if {"en-llm", "en-llm+expand"} & set(arms):
        print(f"перевод через {settings.llm_model}...", flush=True)
        translations = asyncio.run(translate([r["tech"] for r in rows]))

    plan = build_queries(rows, arms, translations)
    queries = embed_plan(plan)

    store = Store(settings.storage_target)

    drop = None
    if args.exclude_junk:
        drop = junk_ids(store)
        print(f"исключено строк без содержания: {len(drop):,}", flush=True)

    corpus = ShardCorpus(EMB_DIR, settings.embedding_model)
    print(f"шардов: {len(corpus)}; проход...", flush=True)

    def tick(done: int, total: int) -> None:
        if done % 20 == 0 or done == total:
            print(f"  шард {done}/{total}", flush=True)

    ids, scores = top_k(corpus, queries, k=args.top, on_shard=tick, exclude=drop)

    meta = resolve(store, ids.ravel().tolist())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(
            ["arm", "n", "tech", "domain", "query", "rank", "score",
             "src", "date", "title", "url", "verdict"]
        )
        for qi, (arm, ri, texts) in enumerate(plan):
            row = rows[ri]
            for rank in range(args.top):
                sid = int(ids[qi, rank])
                if sid < 0:
                    continue
                src, day, title, url = meta.get(sid, ("?", "?", "(нет в signals)", ""))
                w.writerow(
                    [arm, row["n"], row["tech"], row["domain"], " | ".join(texts),
                     rank + 1, f"{float(scores[qi, rank]):.4f}", src, day,
                     (title or "").replace("\t", " ")[:300], url or "", ""]
                )
    print(f"\nTSV: {args.out}")

    report(plan, ids, scores, arms)

    print(
        "\n[!] Приговор выносится глазами по колонке `verdict`, а не по баллу косинуса.\n"
        "    Правило решения зафиксировано ДО прогона: H = hit@20 у лучшего плеча.\n"
        "    H >= 70 — план идёт как есть; 40..69 — классификатор только на привязанной\n"
        "    части, потолок полноты объявляется жюри; H < 40 — разворот конструкции."
    )


if __name__ == "__main__":
    main()
