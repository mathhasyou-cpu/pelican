"""Почему у карточки пустеют «почему сигнал» и «преимущество»: какая проверка снимает
предложения.

Переигрывает промпт карточки (`card.build_prompt`, как `write_cards`) на свидетельствах
сохранённых отчётов (`recard.evidence_of`) и раскладывает каждое предложение сырого ответа
модели по первой сработавшей проверке, в порядке `card._clean`: нет ссылки `[n]` → чужая
письменность → число не из процитированного свидетельства → нет якоря (правило ниже, для
«Преимущества» и «Кейса»). Отдельно — поле, которое модель вернула пустым, и источники без
резюме.

Итог на отчётах 20261012-092107 и 20261013-223355 (30 карточек): якорь снял 12 предложений,
10 из них конкретные — правило из `card.parse` убрано (docs/weak-card.md).

    python scripts/measure_card_drops.py 20261012-092107 20261013-223355

Выход — `reports/card-drops/<отчёт>.jsonl` (сырые ответы) и `reports/card-drops/card-drops.md`.

⚠️ Без `MODEL_LOCK`: модель общая со стендом, параллельный вызов только медленнее, а
время в замер не входит. Отчёты истории не перезаписываются.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from recard import evidence_of  # noqa: E402

from pelican import jobs  # noqa: E402
from pelican.config import settings  # noqa: E402
from pelican.weak import card as card_mod  # noqa: E402
from pelican.weak import page  # noqa: E402
from pelican.weak.ask import CARD_TOKENS, _one  # noqa: E402

OUT = REPO / "reports" / "card-drops"
#: Правило «якоря», которое замер проверял и по итогам которого его из `card.parse` убрали:
#: цифра, компания карточки или латинское имя не из общих терминов и не из названия.
_NAME = re.compile(r"\b[A-Z][A-Za-z0-9]*[a-z0-9][A-Za-z0-9-]*\b|\b[A-Z]{2,}[0-9]+\b")
_GENERIC = frozenset(
    ["AI", "LLM", "LLMs", "ML", "MoE", "UI", "API", "GPU", "CPU", "NPU", "Transformer", "Transformers", "Attention", "Agent", "Agents", "Generative", "Quantum", "Machine", "Learning", "Large", "Language", "Model", "Models", "World", "Deep", "Neural"]
)


def has_anchor(sentence: str, name: str, companies: list[str]) -> bool:
    body = re.sub(r"\[[\d,\s]+\]", " ", sentence)
    if re.search(r"\d", body) or any(c and c in body for c in companies):
        return True
    own = {w.lower() for w in re.findall(r"[A-Za-z]+", name)}
    return any(w not in _GENERIC and w.lower() not in own for w in _NAME.findall(body))
#: Поле → `anchored` в `card.parse`.
FIELDS = {"why": False, "description": False, "advantage": True, "case": True}
REASONS = ("оставлено", "нет [n]", "чужое письмо", "число не из источника", "нет якоря")


def reason(sentence: str, texts: list[str], name: str, companies: list[str], anchored: bool) -> str:
    """Первая проверка `card._clean`, которая снимает предложение (порядок тот же)."""
    nums = [r for r in card_mod.refs(sentence) if 1 <= r <= len(texts)]
    if not nums:
        return "нет [n]"
    if card_mod.foreign_script(sentence):
        return "чужое письмо"
    if card_mod.invented_numbers(sentence, [texts[r - 1] for r in nums]):
        return "число не из источника"
    if anchored and not has_anchor(sentence, name, companies):
        return "нет якоря"
    return "оставлено"


def measure(name: str) -> list[dict]:
    with jobs.connect(settings.database_url) as conn:
        row = jobs.report(conn, name)
    result = page.from_json(row["result"])
    rows, path = [], OUT / f"{name}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for sig in result.signals:
            ev = evidence_of(sig)
            texts = [card_mod.evidence_line(e) for e in ev]
            try:
                payload = asyncio.run(
                    _one(card_mod.SYSTEM_PROMPT, card_mod.build_prompt(sig.title, ev),
                         card_mod.schema(), "card", CARD_TOKENS)
                )  # fmt: skip
            except Exception as exc:  # noqa: BLE001 — сбой одной карточки не валит замер
                print(f"{sig.label}: отказ {type(exc).__name__}: {exc}")
                continue
            r = {"report": name, "label": sig.label, "payload": payload, "fields": {}}
            for field, anchored in FIELDS.items():
                raw = str(payload.get(field) or "")
                sents = card_mod.sentences(raw)
                r["fields"][field] = {
                    "raw_empty": not sents,
                    "sentences": [
                        [s, reason(s, texts, sig.title, list(sig.companies), anchored)] for s in sents
                    ],
                }
            cited = {k for k in (int(i.get("n") or 0) for i in payload.get("summaries") or [])}
            foreign = [i for i, s in enumerate(sig.sources, 1) if s.lang != "ru"]
            r["foreign"], r["foreign_summarized"] = len(foreign), len([i for i in foreign if i in cited])
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            rows.append(r)
            print(f"{name} {sig.label}: " + ", ".join(
                f"{k}={sum(x[1] == 'оставлено' for x in v['sentences'])}/{len(v['sentences'])}"
                for k, v in r["fields"].items()
            ))  # fmt: skip
    return rows


def table(rows: list[dict]) -> str:
    out = [f"Карточек: {len(rows)}", "",
           "| поле | пусто после чистки | пусто у модели | " + " | ".join(REASONS) + " |",
           "|---|---|---|" + "---|" * len(REASONS)]  # fmt: skip
    for field in FIELDS:
        c = Counter(x[1] for r in rows for x in r["fields"][field]["sentences"])
        empty = sum(1 for r in rows if not any(x[1] == "оставлено" for x in r["fields"][field]["sentences"]))
        raw = sum(1 for r in rows if r["fields"][field]["raw_empty"])
        out.append(f"| {field} | {empty} | {raw} | " + " | ".join(str(c[k]) for k in REASONS) + " |")
    fs, fsum = sum(r["foreign"] for r in rows), sum(r["foreign_summarized"] for r in rows)
    out += ["", f"Зарубежных источников: {fs}, с резюме модели: {fsum}"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="причины пустых разделов карточки")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--report-only", action="store_true", help="таблица по готовым jsonl")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.report_only:
        rows = [json.loads(x) for p in sorted(OUT.glob("*.jsonl")) for x in p.read_text("utf-8").splitlines()]
    else:
        rows = [r for n in args.names for r in measure(n)]
    text = table(rows)
    (OUT / "card-drops.md").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
