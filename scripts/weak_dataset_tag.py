"""Часть 1 ТЗ, шаг сырья: привязать 100 названий датасета и назвать приём у найденного.

    python scripts/weak_dataset_tag.py --k 20
    python scripts/weak_dataset_tag.py --k 20 --limit 5      # отладка

Что делает:
1. каждое название датасета (`scripts/weak_signals_100.tsv`) → k ближайших работ
   (`weak.ground`, сырое русское название — лучшее плечо замера);
2. по найденным работам гоняет стадию `tech` — ТОЛЬКО по ним, а не по очереди
   корпуса (модель 0.6 с на аннотацию);
3. пишет `reports/weak-dataset-hits.tsv`: какая работа к какому названию.

⚠️ Привязка «название → работы» в БД не пишется: это разметка ДАТАСЕТА, а не
корпуса, и таблица под неё завела бы в схему то, что живёт один хакатон. Приёмы
же пишутся в `tech_mentions` штатно — это факт о работе, и он пригодится выдаче.
"""

from __future__ import annotations

import argparse
import csv
import io as _io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pelican.config import settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.weak.ground import ground  # noqa: E402
from pelican.weak.tech import run_tech  # noqa: E402

TSV = Path(__file__).resolve().parent / "weak_signals_100.tsv"
OUT = Path("reports/weak-dataset-hits.tsv")


def main() -> None:
    ap = argparse.ArgumentParser(description="привязка датасета и приёмы найденного")
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-tech", action="store_true", help="только привязка, без модели")
    args = ap.parse_args()

    rows = list(csv.DictReader(TSV.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    if args.limit:
        rows = rows[: args.limit]

    store = Store(settings.storage_target)
    t0 = time.time()
    print(f"привязка {len(rows)} названий, k={args.k}...", flush=True)
    hits = ground(store, [r["tech"] for r in rows], k=args.k)
    print(f"  готово за {time.time() - t0:.0f} с", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["n", "rank", "signal_id", "score"])
        for row, found in zip(rows, hits, strict=True):
            for rank, h in enumerate(found, 1):
                w.writerow([row["n"], rank, h.signal_id, f"{h.score:.4f}"])
    ids = sorted({h.signal_id for found in hits for h in found})
    print(f"привязка записана: {OUT} · различных работ {len(ids)}", flush=True)

    if args.no_tech:
        return

    t0 = time.time()

    def on_batch(size: int, error: Exception | None) -> None:
        on_batch.done += size  # type: ignore[attr-defined]
        if error is not None:
            print(f"  пакет упал: {type(error).__name__}: {str(error)[:100]}", flush=True)
        if on_batch.done % 200 < size:  # type: ignore[attr-defined]
            rate = on_batch.done / max(time.time() - t0, 1)  # type: ignore[attr-defined]
            print(f"  приёмов: {on_batch.done} · {rate:.1f} работ/с", flush=True)  # type: ignore[attr-defined]

    on_batch.done = 0  # type: ignore[attr-defined]
    _, stats = run_tech(store, ids=ids, on_batch=on_batch)
    print(
        f"\ntech: в очереди {stats.pending} (прочее размечено) · обработано {stats.processed}"
        f" · с приёмом {stats.named} ({stats.hit_rate:.0%}) · сдвигов раскладки {stats.echo_swap}"
        f" · упавших пакетов {stats.failed_batches} · {time.time() - t0:.0f} с"
    )
    print("роли:", stats.by_role)


if __name__ == "__main__":
    main()
