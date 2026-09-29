"""Трудный контроль для замера жанра — из собственной выдачи, а не из головы.

## Зачем трудный

Лёгкий контроль (`scripts/weak_controls.tsv`) написан нами и состоит из очевидного:
«банкоматы», «Индустрия 4.0», «ПИД-регуляторы». Модель не принимает за сигнал ни одну
такую строку, и «0 ложных из 30» на них не доказывает почти ничего ([todo](../docs/todo.md)
§61). Трудный случай — свежая, но уже занятая ниша и рыночная категория с деньгами: то,
что от сигнала отличается не словарём, а сутью.

## Откуда берутся строки: три источника, и провенанс пишется в файл

1. **Рука** — карточки, которым человек в `scripts/precision_pairs.tsv` поставил «нет».
   Единственная честная разметка, какая у нас есть; её и надо набирать.
2. **Зрелое ядро по следу корпуса** — направления нашего же ТОП, у которых ядро набрало
   много работ и появилось давно (`MATURE_WORKS`, `MATURE_SINCE`).

   ⚠️ **Меткой это НЕ становится, только кандидатом на разметку.** Зрелое ядро в НОВОМ
   применении — сам по себе трудный случай, и правильный ответ там «сигнал»: у «долгосрочной
   памяти для ИИ-агентов» ядро `knowledge graph` набрало 4483 работы с 2013, а направление
   новое. Записать такую строку в контроль как `mature` значило бы наказывать модель за
   верный ответ. Поэтому кандидаты уходят в `reports/hard-control-candidates.tsv` — на глаза
   человеку, — а в контроль попадает только подтверждённое рукой.
3. **Вердикт судьи** `mature`/`market`. ⚠️ Замерено: источник почти пустой — на 73 карточках
   судья дал ровно один такой вердикт, потому что сам насыщен (`docs/weak-audit.md`).

⚠️ **Провенанс каждой строки пишется в файл колонкой `откуда`**: смешивать руку с машиной в
одном числе нельзя, а разделить их потом можно только если записано сразу.

⚠️ **Строки берутся РУССКИМ заголовком карточки**, а не английским ярлыком: положительные
в датасете русские, и контроль обязан совпадать с ними по форме названия — иначе классы
разойдутся по языку и стилю, а не по сути (то же правило, что у лёгкого контроля).

    python scripts/make_hard_controls.py
    python scripts/measure_signal_kinds.py --evidence mixed \\
        --controls-file scripts/weak_controls_hard.tsv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VERDICTS = REPO / "reports" / "judge-precision.tsv"
PAIRS = REPO / "scripts" / "precision_pairs.tsv"
OUT = REPO / "scripts" / "weak_controls_hard.tsv"

#: Вердикт судьи → класс, которого мы ждём от оценщика жанра (`weak/kinds.py`).
EXPECTED = {"mature": "mature", "market": "too_broad"}
#: Когда след ядра считается зрелым. ⚠️ Догадка, зажатая с двух сторон: ниже — в контроль
#: попадут настоящие сигналы, выше — не наберётся строк.
MATURE_WORKS = 3000
MATURE_SINCE = 2019


def cards() -> dict[tuple[str, str], dict]:
    """(домен, английский ярлык) → карточка целиком."""
    out = {}
    for path in (REPO / "reports").glob("ask-cards-*.json"):
        domain = path.stem.removeprefix("ask-cards-").replace("-", " ")
        run = json.loads(path.read_text(encoding="utf-8"))
        for sig in run.get("signals", []):
            out[(domain, sig.get("label", ""))] = sig
    return out


def title_of(sig: dict) -> str:
    return sig.get("title") or sig.get("label", "")


def from_hand(known: dict[tuple[str, str], dict]) -> list[tuple[str, str, str, str]]:
    """Карточки, которым человек сказал «нет». ⚠️ Единственная честная разметка."""
    if not PAIRS.exists():
        return []
    rows = list(csv.DictReader(PAIRS.read_text(encoding="utf-8").splitlines(), delimiter=chr(9)))
    out = []
    for r in rows:
        answer = (r.get("годится? да/нет") or "").strip().lower()
        if not answer or answer.startswith("д"):
            continue
        sig = known.get((r.get("домен", ""), r.get("направление", "")))
        why = (r.get("если нет — почему") or "").lower()
        expected = "too_broad" if "рынок" in why else ("noise" if "шум" in why else "mature")
        name = title_of(sig) if sig else r.get("название (ru)", "")
        out.append((name, r["домен"], expected, "рука"))
    return out


def from_footprint(known: dict[tuple[str, str], dict]) -> list[tuple[str, str, str, str]]:
    """Направления со зрелым ядром: много работ и давнее начало. Признак замеренный."""
    out = []
    for (domain, _label), sig in known.items():
        since = sig.get("core_since")
        if (sig.get("core_works") or 0) >= MATURE_WORKS and since and int(since) <= MATURE_SINCE:
            why = f"след ядра: {sig['core_works']} работ с {since}"
            out.append((title_of(sig), domain, "mature", why))
    return out


def from_judge(known: dict[tuple[str, str], dict]) -> list[tuple[str, str, str, str]]:
    """Вердикты судьи `mature`/`market`. ⚠️ Источник почти пустой — судья насыщен."""
    if not VERDICTS.exists():
        return []
    rows = list(csv.DictReader(VERDICTS.read_text(encoding="utf-8").splitlines(), delimiter=chr(9)))
    out = []
    for r in rows:
        expected = EXPECTED.get(r["вердикт"])
        if not expected:
            continue
        sig = known.get((r["домен"], r["карточка"]))
        name = title_of(sig) if sig else r["карточка"]
        out.append((name, r["домен"], expected, "судья: " + r["вердикт"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="трудный контроль из собственной выдачи")
    ap.add_argument("--limit", type=int, default=30, help="сколько строк выписать")
    args = ap.parse_args()

    known = cards()
    if not known:
        print("нет reports/ask-cards-*.json: сначала прогон measure_ask.py --domains")
        return

    picked: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    # В контроль идёт ТОЛЬКО размеченное рукой; машинные метки — кандидаты на разметку.
    for name, domain, expected, why in from_hand(known):
        if not name or name in seen:
            continue
        seen.add(name)
        picked.append((name, domain, expected, why))

    candidates = [
        row
        for row in (*from_judge(known), *from_footprint(known))
        if row[0] and row[0] not in seen
    ]
    cand_path = REPO / "reports" / "hard-control-candidates.tsv"
    cand_path.parent.mkdir(parents=True, exist_ok=True)
    with cand_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=chr(9), lineterminator=chr(10))
        w.writerow(["направление", "домен", "похоже на", "почему", "мой вердикт"])
        for name, domain, expected, why in candidates:
            w.writerow([name, domain, expected, why, ""])
    print(f"кандидатов на разметку: {len(candidates)} → {cand_path}")

    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=chr(9), lineterminator=chr(10))
        w.writerow(["n", "tech", "domain", "expected", "откуда"])
        for i, (name, domain, expected, why) in enumerate(picked[: args.limit], 1):
            w.writerow([f"h{i}", name, domain, expected, why])

    kept = picked[: args.limit]
    print(f"выписано {len(kept)} трудных строк: {OUT}")
    by_source: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for _n, _d, expected, why in kept:
        key = why.split(":")[0]
        by_source[key] = by_source.get(key, 0) + 1
        by_class[expected] = by_class.get(expected, 0) + 1
    print("  по источнику:", by_source)
    print("  по классам:", by_class)
    if not kept:
        print(
            "⚠️ контроль ПУСТ: рукой не размечено ни одной строки. Проставьте «нет» в"
            " scripts/precision_pairs.tsv или вердикты в reports/hard-control-candidates.tsv"
        )
    if 0 < len(kept) < 10:
        print("⚠️ строк мало: доля ложных на таком контроле известна хуже чем ±0.3")


if __name__ == "__main__":
    main()
