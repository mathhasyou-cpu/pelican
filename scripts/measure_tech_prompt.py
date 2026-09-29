"""Замер промпта `tech`: называет ли модель приём, а не тему и не имя системы.

    python scripts/measure_tech_prompt.py --sample 200
    python scripts/measure_tech_prompt.py --sample 60 --show 60   # с полной выдачей

⚠️ В БД не пишет ничего, и это условие работы, а не осторожность: стадия ещё не
закреплена, и прогон затёр бы разметку, с которой потом сравнивать. `Store`
открывается через общий DSN, но только на чтение.

## Правило решения, зафиксированное ДО прогона

Доля строк, где названа реальная технология, а не тема и не пересказ. **Ниже
половины — промпт не годится, и чинить надо его, а не пороги ниже по конвейеру.**

Считается по выдаче глазами, а не по счётчику `named`: счётчик говорит лишь, что
модель что-то вернула и оно прошло `clean_tech`. Настоящий вопрос — приём ли это.

## Что печатается

- доля непустых, раскладка по роли (`proposes` / `uses` / `reviews`);
- причины отказа `clean_tech` — по ним видно, ЧЕМ модель промахивается;
- ⚠️ **повторяемость**: сколько различных приёмов на выборку. Если модель на сто
  разных работ выдаёт двадцать формулировок, она называет тему, а не приём, и
  это видно раньше, чем глазами;
- выборка строк «вход → приём» для чтения.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import io as _io
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Консоль планировщика и Git Bash приезжают в cp1251, а вывод здесь русский.
sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pelican.config import settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.llm import Breaker, LLMClient, LLMError  # noqa: E402
# noqa: E402
from pelican.weak.tech import (  # noqa: E402
    BATCH_SIZE,
    SYSTEM_PROMPT,
    Named,
    Pending,
    TechStats,
    batch_schema,
    batch_token_budget,
    build_prompt,
    parse_batch,
)

#: Откуда берётся выборка. ⚠️ Только внутри окна, покрытого векторами
#: (`emb-science/state.json`, знак на 2023-07-27): замер должен идти по той же
#: популяции, на которой потом будет работать привязка.
SINCE = "2023-08-01"


def sample(store: Store, n: int, seed: int) -> list[Pending]:
    """Выборка, повторяемая по seed.

    ⚠️ **`USING SAMPLE` через Quack молча отдаёт НОЛЬ строк** — без ошибки, без
    предупреждения, и это неотличимо от «под фильтр ничего не подошло» (замер
    15.09: тот же запрос по файлу работает). Поэтому выборка делается
    сортировкой по хэшу: она и повторяема по seed, и идёт обычным планом.
    Цена — полный проход, около 18 с на 2.9 млн строк; для замера это приемлемо.
    """
    sql = f"""
    SELECT id, term_raw FROM works
    WHERE source IN ('arxiv','openalex')
      AND observed_at >= DATE '{SINCE}'
      AND NOT junk
      AND length(term_raw) >= 120
    ORDER BY hash(id + {seed}) LIMIT {n}
    """
    return [Pending(signal_id=int(r[0]), text=r[1]) for r in store.conn.execute(sql).fetchall()]


async def run(batches: list[list[Pending]], stats: TechStats) -> list[Named]:
    client = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
    breaker = Breaker()
    out: list[Named] = []
    async with httpx.AsyncClient() as http:
        for i, batch in enumerate(batches, 1):
            try:
                payload = await client.json_completion(
                    http,
                    system=SYSTEM_PROMPT,
                    user=build_prompt(batch),
                    schema=batch_schema(len(batch)),
                    name="techs",
                    max_tokens=batch_token_budget(batch),
                )
                out += parse_batch(payload, batch, stats)
                stats.processed += len(batch)
                breaker.record(None)
            except (LLMError, ValueError, httpx.HTTPError) as exc:
                stats.failed_batches += 1
                print(f"  пакет {i} упал: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                # Предохранитель считает ТОЛЬКО отказы соединения: ошибка разбора
                # ничего не говорит о доступности модели (`llm.client.Breaker`).
                if breaker.record(exc):
                    stats.unreachable = True
                    break
            if i % 5 == 0:
                print(f"  пакетов {i}/{len(batches)}", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="замер промпта tech")
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--batch", type=int, default=BATCH_SIZE)
    ap.add_argument("--seed", type=int, default=20260915)
    ap.add_argument("--show", type=int, default=40)
    args = ap.parse_args()

    store = Store(settings.storage_target)
    rows = sample(store, args.sample, args.seed)
    print(f"выборка: {len(rows)} аннотаций с {SINCE}, модель {settings.llm_model}")

    stats = TechStats(pending=len(rows))
    batches = [rows[i : i + args.batch] for i in range(0, len(rows), args.batch)]
    named = asyncio.run(run(batches, stats))

    print(f"\nобработано {stats.processed} · с приёмом {stats.named} ({stats.hit_rate:.0%}) "
          f"· пусто {stats.empty} · упавших пакетов {stats.failed_batches}")
    if stats.unreachable:
        print("[!] модель недоступна — предохранитель разомкнул прогон")
    print("роли:", dict(sorted(stats.by_role.items(), key=lambda kv: -kv[1])))
    if stats.rejected:
        print("отказы clean_tech:", dict(sorted(stats.rejected.items(), key=lambda kv: -kv[1])))
    print(f"эхо разошлось: {stats.echo_drift} (из них сдвиг раскладки: {stats.echo_swap})")

    hits = [n for n in named if n.tech]
    if hits:
        uniq = {n.tech.lower() for n in hits}
        print(f"\nразличных приёмов {len(uniq)} на {len(hits)} названных "
              f"({len(uniq) / len(hits):.0%} — чем ближе к 100%, тем конкретнее)")
        dupes = collections.Counter(n.tech.lower() for n in hits)
        repeated = [(name, c) for name, c in dupes.most_common(8) if c > 1]
        if repeated:
            print("повторяющиеся:", repeated)
    else:
        print("\n[!] названных приёмов НЕТ ВОВСЕ — смотреть отказы, а не выдачу ниже")

    print(f"\n--- выборка на чтение глазами ({min(args.show, len(named))}) ---")
    for n in named[: args.show]:
        mark = f"[{n.role}]" if n.tech else "[—]"
        print(f"\n  ВХОД: {n.text[:150]}")
        print(f"  ПРИЁМ {mark}: {n.tech or '(пусто)'}")

    print(
        "\n[!] Правило решения: доля строк, где названа РЕАЛЬНАЯ технология, а не тема\n"
        "    и не имя системы. Ниже половины — чинить промпт, а не пороги."
    )


if __name__ == "__main__":
    main()
