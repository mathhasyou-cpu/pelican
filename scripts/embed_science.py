"""Векторы научного корпуса: `arxiv` + `openalex`, от нового к старому.

    python scripts/embed_science.py               # весь корпус, до Ctrl+C
    python scripts/embed_science.py --limit 5000  # проверка на малом объёме
    python scripts/embed_science.py --measure     # замер пропускной способности

Аннотации в конвейер не входят (`docs/sources-science.md`), и разобрать их
локальной моделью нельзя по цене: замер gemma-4-12b — 0.60 с на аннотацию, то
есть 640 часов GPU на 3.86 млн строк. Эмбеддер на два порядка дешевле, а векторы
нужны любому дальнейшему решению: соседи, кластеры тем, ANN-поиск. Поэтому
корпус векторизуется заранее и целиком.

## Порядок — keyset pagination («seek method»)

Строки идут `ORDER BY observed_at DESC, id DESC`, следующая страница берётся
предикатом `(observed_at, id) < (знак)`, а не `OFFSET`: на миллионах строк
`OFFSET` перечитывает всё, что пропускает, и деградирует квадратично
(https://use-the-index-luke.com/no-offset).

## Что посчитано — говорят ШАРДЫ, а не водяной знак

⚠️ **Прогон каждый раз идёт от самого свежего, а уже посчитанные строки
пропускаются по id из шардов.** Знак в `state.json` — подсказка о глубине, и
решать по нему, что делать дальше, нельзя: строка, добавленная в корпус ПОЗЖЕ,
но с датой ВЫШЕ знака, курсором уже пройдена, и возобновление по знаку не взяло
бы её никогда. Замер 2026-09-17 показал ровно это: знак стоял на 2023-07-27,
на диске лежало 2.16 млн векторов, а годных строк выше знака было 5.96 млн —
то есть **3.96 млн строк (2024: 829k, 2025: 1.73 млн, 2026: 1.40 млн) не имели
вектора и не были бы взяты никогда**. Поиск при этом не ошибается, а молча
находит меньше соседей, и заметить это можно только такой сверкой.

Пропуск посчитанного стоит одного чтения страницы из базы и ничего не стоит на
видеокарте, поэтому обход от свежего к старому остаётся дешёвым и после того,
как половина корпуса уже посчитана.

⚠️ **Оба источника идут ОДНИМ отсортированным потоком.** Не по источнику подряд:
прогон обрывается рукой, и обрыв обязан оставлять ровный ВРЕМЕННОЙ срез по обоим,
а не arxiv целиком и openalex пустым. Тем же приёмом и по той же причине
переставлены циклы в `sources/openalex.py:fetch_stream`.

## Хранилище — шарды на диске, а не таблица

В базу класть нельзя: 3.86 млн × 768 float32 = 11.6 ГБ поверх и без того
тринадцати, при одном писателе на файл (`docs/storage-engine.md`). Один общий
`.npz`, как `RAW_CACHE` в `search_corpus.py`, тоже не годится — он переписывается
целиком на каждом сохранении, а здесь сохранений две сотни.

Поэтому `<DATA_DIR>/emb-science/shard-000123.npz`: `ids` (int64), `vectors`
(float16), `model` (строка). Имя модели проверяется СОДЕРЖИМЫМ, а не именем
файла — приём из `search_corpus.py`: векторы другой модели молча дали бы
бессмысленные косинусы.

⚠️ **float16 — догадка, а не замер** (`docs/todo.md`). Вектор нормирован, живёт в
[-1,1], и половинная точность даёт ошибку косинуса порядка 1e-3 — для поиска
соседей это заведомо ничто, но замера на нашем корпусе нет. Память при этом
пополам: 5.8 ГБ против 11.6 на весь корпус.

⚠️ **Префикс задачи EmbeddingGemma здесь не применяется** — по той же причине,
что и в `search_corpus.py` (см. `QUERY_PROMPT` там): векторы обязаны быть
сопоставимы с `need_embeddings`, которые `cluster` пишет без префикса.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Консоль планировщика и Git Bash приезжают в cp1251, а вывод здесь русский.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pelican import locks  # noqa: E402
from pelican.config import EMB_DIR, settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.llm.client import with_retries  # noqa: E402
from pelican.embed import embed  # noqa: E402

#: Источники, которые копят корпус и в конвейер не входят.
SOURCES = ("arxiv", "openalex")

#: Куда складывать шарды. Рядом с базой, на том же томе, не коммитится.
OUT_DIR = EMB_DIR
STATE = OUT_DIR / "state.json"

#: Строк в шарде. 20 000 × 768 × 2 байта = 30 МБ на файл: достаточно крупно,
#: чтобы файлов были сотни, а не десятки тысяч, и достаточно мелко, чтобы
#: прерывание стоило минут, а не часов.
SHARD_ROWS = 20_000

#: Сколько строк тянуть из базы за раз. Не равно шарду: страница базы дешевле
#: шарда и совпадать с ним не обязана.
PAGE_ROWS = 5_000

#: Потолок текста. `term_raw` — заголовок плюс первые 240 символов аннотации
#: (`sources/base.py`), но у openalex попадаются заголовки-абзацы (максимум по
#: корпусу — 5 057 символов), а у эмбеддера контекст 2 048 токенов.
MAX_CHARS = 1500


def vector_ids() -> np.ndarray:
    """Отсортированные id всего, что уже посчитано, — по самим шардам.

    Источник истины о проделанной работе здесь один: файлы на диске. Знак в
    `state.json` описывает только глубину обхода (см. модульную заметку).
    """
    out = [np.load(p)["ids"] for p in sorted(OUT_DIR.glob("shard-*.npz"))]
    return np.unique(np.concatenate(out)) if out else np.zeros(0, dtype=np.int64)


def _has_vector(have: np.ndarray, row_id: int) -> bool:
    """Двоичный поиск по отсортированным id: множество на 2 млн int тут дороже."""
    i = int(np.searchsorted(have, row_id))
    return i < have.size and bool(have[i] == row_id)


def next_shard_index() -> int:
    """Номер следующего шарда — по занятым именам, а не по счётчику в состоянии.

    ⚠️ Отставший счётчик молча ЗАТЁР бы готовый шард: имя файла определяется им.
    """
    used = [int(p.stem.rsplit("-", 1)[1]) for p in OUT_DIR.glob("shard-*.npz")]
    return max(used) + 1 if used else 0


def load_state() -> dict:
    """Знак прошлого прогона: глубина и имя модели. Полноту он НЕ описывает."""
    if not STATE.exists():
        return {}
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if state.get("model") != settings.embedding_model:
        raise SystemExit(
            f"в {STATE} знак модели {state.get('model')!r}, а считать собрались "
            f"{settings.embedding_model!r}: смешивать векторы разных моделей нельзя"
        )
    return state


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def sources_sql() -> str:
    return ", ".join("'" + s + "'" for s in SOURCES)


def page(store: Store, mark: dict | None, size: int) -> list[tuple]:
    """Страница корпуса от знака вглубь. Свежие — первыми."""
    where = f"source IN ({sources_sql()})"
    params: list = []
    if mark:
        where += " AND (observed_at, id) < (?, ?)"
        params = [datetime.fromisoformat(mark["observed_at"]), mark["id"]]
    return store.query(
        f"SELECT id, observed_at, term_raw FROM works WHERE {where} "
        f"ORDER BY observed_at DESC, id DESC LIMIT {int(size)}",
        params,
    )


@with_retries
def embed_chunk(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    """Один пакет. Ретрай здесь, а не внутри `embed`: прогон идёт часами, и одна
    сетевая икота не имеет права его ронять."""
    return embed(client, texts, settings.embedding_model, len(texts))


def flush(
    ids: list[int],
    vectors: list[list[float]],
    index: int,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Path:
    """Шард на диск. Пишется целиком и ровно один раз.

    ⚠️ **Номер шарда — счётчик свободных имён, а не место во времени.** Готовый
    файл не открывается никогда, а новый прогон берёт следующий свободный номер,
    поэтому `shard-000000` держит свежак ПЕРВОГО прогона, а свежак сегодняшнего
    лежит на две сотни номеров дальше. Судить о датах шарда по его имени нельзя.

    Поэтому границы `observed_at` кладутся ВНУТРЬ файла: обход идёт по времени,
    и шард — непрерывный кусок календаря, но снаружи это знание взять негде.
    Два числа дают as-of прогону пропускать шарды целиком вместо маски на
    миллионы id (`weak.corpus.future_ids`).
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"shard-{index:06d}.npz"
    extra = {}
    if since is not None and until is not None:
        extra = {
            "since": np.array(since.date().isoformat()),
            "until": np.array(until.date().isoformat()),
        }
    np.savez(
        out,
        model=np.array(settings.embedding_model),
        ids=np.array(ids, dtype=np.int64),
        vectors=np.asarray(vectors, dtype=np.float16),
        **extra,
    )
    return out


def measure(store: Store) -> int:
    """Шаг 0: пропускная способность на прогретой модели.

    CLAUDE.md: порог подбирается замером. Первый вызов включает загрузку модели
    и потому в замер не идёт — прогреваем отдельно.
    """
    rows = page(store, None, 1024)
    texts = [r[2][:MAX_CHARS] for r in rows]
    with httpx.Client(timeout=settings.llm_timeout_s) as client:
        embed_chunk(client, texts[:32])  # прогрев
        for size in (64, 128, 256):
            best = 0.0
            for _ in range(2):
                started = time.monotonic()
                embed_chunk(client, texts[:size])
                best = max(best, size / (time.monotonic() - started))
            print(f"пакет {size:>4}: {best:7.1f} текстов/с")
    return 0


def run(store: Store, limit: int | None) -> int:
    state = load_state()
    total = store.query(f"SELECT count(*) FROM works WHERE source IN ({sources_sql()})")[0][0]
    have = vector_ids()
    done = int(have.size)
    index = next_shard_index()
    size = settings.embedding_batch_size
    print(
        f"корпус {total}, уже посчитано {done}, осталось {total - done}, "
        f"пакет {size}, следующий шард {index}"
    )

    ids: list[int] = []
    vectors: list[list[float]] = []
    # ⚠️ Обход ВСЕГДА от самого свежего: см. модульную заметку про водяной знак.
    mark: dict | None = None
    skipped = 0
    shown = 0  # сколько пропусков уже попало в лог: остаток — доля текущего шарда
    # Границы времени НАКОПЛЕННОГО шарда: считаются по строкам, попавшим в него,
    # а не по знаку обхода — пропущенные готовые в шард не входят.
    lo: datetime | None = None
    hi: datetime | None = None
    started = time.monotonic()
    at_start = done

    def store_shard() -> None:
        nonlocal ids, vectors, index, shown, lo, hi
        if not ids or mark is None:
            return
        out = flush(ids, vectors, index, since=lo, until=hi)
        index += 1
        state.update(
            model=settings.embedding_model,
            observed_at=mark["observed_at"],
            id=mark["id"],
            done=done,
            shards=index,
        )
        save_state(state)
        rate = (done - at_start) / max(time.monotonic() - started, 1e-9)
        left = (total - done) / rate / 3600 if rate else float("inf")
        # ⚠️ Пропуск печатается и за шард, и накопительно: одно число рядом с
        # размером шарда читается как доля этого шарда, а это не она.
        print(
            f"{out.name}: {len(ids)} строк, всего {done}/{total} "
            f"({rate:.0f}/с, до конца ~{left:.1f} ч), готовых пропущено "
            f"{skipped - shown} за шард / {skipped} за прогон, "
            f"глубина {mark['observed_at'][:10]}"
        )
        ids, vectors = [], []
        lo = hi = None
        shown = skipped

    try:
        with httpx.Client(timeout=settings.llm_timeout_s) as client:
            while limit is None or done - at_start < limit:
                rows = page(store, mark, PAGE_ROWS)
                if not rows:
                    print("корпус кончился")
                    break
                # ⚠️ Знак двигается по ВСЕЙ странице, а не по пересчитанной её
                # части: иначе уже посчитанная страница остановила бы обход.
                mark = {"observed_at": rows[-1][1].isoformat(), "id": rows[-1][0]}
                fresh = [r for r in rows if not _has_vector(have, r[0])]
                skipped += len(rows) - len(fresh)
                for start in range(0, len(fresh), size):
                    chunk = fresh[start : start + size]
                    vectors.extend(embed_chunk(client, [r[2][:MAX_CHARS] for r in chunk]))
                    ids.extend(r[0] for r in chunk)
                    days = [r[1] for r in chunk]
                    lo = min(days) if lo is None else min(lo, *days)
                    hi = max(days) if hi is None else max(hi, *days)
                    done += len(chunk)
                    if len(ids) >= SHARD_ROWS:
                        store_shard()
                    if limit is not None and done - at_start >= limit:
                        break
    except KeyboardInterrupt:
        # ⚠️ Недосчитанный шард дописывается, а не бросается: двадцать тысяч
        # строк — это минуты GPU, и терять их на каждом Ctrl+C незачем.
        print("\nпрервано рукой, дописываю начатый шард")
    finally:
        store_shard()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Векторы научного корпуса")
    parser.add_argument("--limit", type=int, default=None, help="Сколько строк за прогон")
    parser.add_argument("--measure", action="store_true", help="Только замер скорости")
    args = parser.parse_args()

    # Замок общий с модельными стадиями: видеокарта одна, и ночной `trends llm`
    # обязан честно упасть кодом 75, а не драться за неё (`cli.py:LOCK_MODEL`).
    try:
        with locks.hold("model"), Store(settings.storage_target) as store:
            return measure(store) if args.measure else run(store, args.limit)
    except locks.Busy as exc:
        print(f"занято: {exc}")
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
