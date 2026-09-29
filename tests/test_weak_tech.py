"""Стадия tech: разбор ответа модели, отсев имён систем и эхо префиксом."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pelican.store import Store
from pelican.sources.base import Signal
from pelican.weak.tech import (
    PROPOSES,
    REVIEWS,
    USES,
    Pending,
    TechStats,
    batch_schema,
    clean_tech,
    is_category,
    parse_batch,
    query_words,
    same_head,
)


def _pending(*texts: str) -> list[Pending]:
    return [Pending(signal_id=i + 1, text=t) for i, t in enumerate(texts)]


@pytest.mark.parametrize(
    ("raw", "tech", "reason"),
    [
        (
            "speculative decoding with flash offloading",
            "speculative decoding with flash offloading",
            "",
        ),
        ('  "electrohydraulic   artificial muscles" ', "electrohydraulic artificial muscles", ""),
        ("", "", ""),
        # ⚠️ Однословный ответ — имя системы или категория, по корпусу не считается.
        ("MemSentry", "", "одно слово: имя системы или категория"),
        ("machine learning", "", "категория, а не приём"),
        ("a b c d e f g h i", "", "пересказ аннотации"),
    ],
)
def test_clean_tech(raw: str, tech: str, reason: str) -> None:
    assert clean_tech(raw) == (tech, reason)


def test_same_head_accepts_one_word_short_echo() -> None:
    """Замер: модель стабильно возвращает на слово меньше запрошенных шести.
    Строгое сравнение объявляло это расхождением в 37.5% выборки."""
    assert same_head(
        "Assessment of building vulnerability to tsunamis:",
        "Assessment of building vulnerability to",
    )


def test_same_head_rejects_foreign_and_too_short() -> None:
    assert not same_head(
        "Large Kernel Modulation Network for Efficient", "Collective rising dynamics"
    )
    assert not same_head("Large Kernel Modulation Network for Efficient", "Large Kernel")


def test_parse_batch_counts_roles_and_empties() -> None:
    batch = _pending(
        "GPT-Red: Automated Red Teaming via Self-Play at Scale. We introduce",
        "Structural constraints on public transport use in the EU",
        "Graph Neural Networks for Threat Intelligence: A Survey and Future",
        "Gödel's Incompleteness: The Unbridgeable Gap Between Truth and Proof",
    )
    payload = {
        "items": [
            {
                "head": "GPT-Red: Automated Red Teaming via",
                "tech": "automated red teaming via self-play",
                "role": "proposes",
            },
            {
                "head": "Structural constraints on public transport",
                "tech": "panel VAR study",
                "role": "uses",
            },
            {
                "head": "Graph Neural Networks for Threat",
                "tech": "graph neural networks",
                "role": "reviews",
            },
            {"head": "Gödel's Incompleteness: The Unbridgeable Gap", "tech": "", "role": "none"},
        ]
    }
    stats = TechStats()
    out = parse_batch(payload, batch, stats)

    assert [n.role for n in out] == [PROPOSES, USES, REVIEWS, "none"]
    assert out[3].tech == ""
    assert stats.named == 3 and stats.empty == 1
    assert stats.by_role == {PROPOSES: 1, USES: 1, REVIEWS: 1}
    assert stats.echo_drift == 0 and stats.echo_swap == 0


def test_parse_batch_flags_layout_shift() -> None:
    """Эхо чужого входа того же пакета — улика сдвига раскладки, а не пересказа."""
    batch = _pending(
        "Large Kernel Modulation Network for Efficient Image Super-Resolution",
        "Collective rising dynamics of four bubbles under surface tension",
    )
    payload = {
        "items": [
            {
                "head": "Collective rising dynamics of four bubbles",
                "tech": "adaptive refinement sph",
                "role": "uses",
            },
            {
                "head": "Large Kernel Modulation Network for Efficient",
                "tech": "large kernel modulation network",
                "role": "proposes",
            },
        ]
    }
    stats = TechStats()
    parse_batch(payload, batch, stats)
    assert stats.echo_drift == 2
    assert stats.echo_swap == 2


def test_parse_batch_rejects_wrong_length() -> None:
    with pytest.raises(ValueError):
        parse_batch({"items": []}, _pending("one two three four five six"), TechStats())


def test_system_name_is_rejected_not_counted() -> None:
    stats = TechStats()
    out = parse_batch(
        {
            "items": [
                {
                    "head": "MemSentry: A Framework for Detecting",
                    "tech": "MemSentry",
                    "role": "proposes",
                }
            ]
        },
        _pending("MemSentry: A Framework for Detecting Persistent Memory Poisoning"),
        stats,
    )
    assert out[0].tech == ""
    assert stats.named == 0
    assert stats.rejected == {"одно слово: имя системы или категория": 1}


def test_schema_pins_batch_length() -> None:
    schema = batch_schema(5)
    items = schema["properties"]["items"]
    assert items["minItems"] == items["maxItems"] == 5
    assert items["items"]["properties"]["role"]["enum"] == ["proposes", "uses", "reviews", "none"]


# ------------------------------------------------------------------ очередь в БД

NOW = datetime.now(UTC)
LONG = "Title of a real paper. " + "An abstract that goes on long enough to count as content. " * 3


def _sci(
    source: str, ext: str, text: str = LONG, kind: str = "article", when: datetime | None = None
) -> Signal:
    return Signal(
        source=source,
        external_id=ext,
        term_raw=text,
        observed_at=when or NOW - timedelta(days=3),
        metric="works",
        value=None,
        payload={"type": kind},
    )


def test_queue_takes_only_science(store: Store) -> None:
    store.insert_signals(
        [
            _sci("arxiv", "qs-a1"),
            _sci("openalex", "qs-o1"),
            Signal("hackernews", "qs-hn1", LONG, NOW - timedelta(days=1), "count", 1.0),
        ]
    )
    queue = store.untagged_science("m-qs", since=NOW - timedelta(days=30))
    assert len(queue) == 2


def test_queue_skips_rows_without_content(store: Store) -> None:
    """Словарные статьи и голые заголовки openalex в очередь модели не идут."""
    store.insert_signals(
        [
            _sci("openalex", "sk-ok"),
            _sci("openalex", "sk-short", text="Machine Learning"),
            _sci("openalex", "sk-ref", kind="reference-entry"),
            # У arxiv правило не действует: коротких строк там 4 на миллион.
            _sci("arxiv", "sk-short-arxiv", text="Short arxiv title"),
        ]
    )
    queue = {row[1] for row in store.untagged_science("m-sk", since=NOW - timedelta(days=30))}
    assert LONG in queue
    assert "Machine Learning" not in queue
    assert "Short arxiv title" in queue
    assert len(queue) == 2


def test_queue_by_ids_and_resume(store: Store) -> None:
    store.insert_signals([_sci("arxiv", "ir-a1"), _sci("arxiv", "ir-a2", text=LONG + " two")])
    all_ids = [row[0] for row in store.untagged_science("m-ir", since=NOW - timedelta(days=30))]
    assert len(all_ids) == 2

    only = store.untagged_science("m-ir", since=NOW - timedelta(days=30), ids=[all_ids[0]])
    assert [row[0] for row in only] == [all_ids[0]]
    assert store.untagged_science("m-ir", since=NOW - timedelta(days=30), ids=[]) == []

    store.save_tech("m-ir", [(all_ids[0], "automated red teaming", "proposes")])
    store.save_tech("m-ir", [(all_ids[0], "automated red teaming", "proposes")])  # идемпотентно
    left = [row[0] for row in store.untagged_science("m-ir", since=NOW - timedelta(days=30))]
    assert left == [all_ids[1]]
    assert dict(store.tech_counts("m-ir")) == {"proposes": 1}


def test_distinct_techs_keeps_roles_apart(store: Store) -> None:
    store.insert_signals([_sci("arxiv", f"dr-a{i}", text=f"{LONG} {i}") for i in range(3)])
    ids = [row[0] for row in store.untagged_science("m-dr", since=NOW - timedelta(days=30))]
    store.save_tech(
        "m-dr",
        [
            (ids[0], "event-based vision sensors", "proposes"),
            (ids[1], "event-based vision sensors", "uses"),
            (ids[2], "event-based vision sensors", "reviews"),
        ],
    )
    assert store.distinct_techs("m-dr") == [("event-based vision sensors", 1, 1, 1)]


def test_daily_counts_drop_january_first(store: Store) -> None:
    """1 января у openalex — заглушка вместо даты, а не наблюдение."""
    stub = datetime(2025, 1, 1, tzinfo=UTC)
    real = datetime(2025, 1, 2, tzinfo=UTC)
    store.insert_signals(
        [
            _sci("openalex", "jf-stub", text=LONG + " stub", when=stub),
            _sci("openalex", "jf-real", text=LONG + " real", when=real),
        ]
    )
    ids = [row[0] for row in store.untagged_science("m-jf", since=datetime(2024, 1, 1, tzinfo=UTC))]
    store.save_tech("m-jf", [(i, "memory poisoning defense", "proposes") for i in ids])

    days = [row[2] for row in store.tech_daily_counts("m-jf")]
    assert len(days) == 1
    assert days[0].month == 1 and days[0].day == 2


def test_market_category_has_nothing_beyond_the_query() -> None:
    words = query_words("AI security", "artificial intelligence security")
    assert is_category("enterprise ai software security", words)
    assert is_category("ai security and threat protection", words)
    # ⚠️ «agent» — эхо эпохи: одно, без механизма рядом, направления не называет.
    assert is_category("autonomous ai security agents", words)


def test_a_named_mechanism_survives_the_same_query() -> None:
    words = query_words("AI security", "artificial intelligence security")
    for label in (
        "identity and access management for ai agents",
        "security scanners for mcp servers",
        "machine unlearning for model risk remediation",
        "llm firewalls at the network edge",
    ):
        assert not is_category(label, words), label


def test_hyphen_is_not_a_hiding_place() -> None:
    # «multi-agent» состоит из слов запроса и рынка, дефис этого не меняет.
    words = query_words("финтех", "financial technology", "banking", "finance")
    assert is_category("multi-agent systems for financial services", words)
    assert not is_category("post-quantum cryptography in financial infrastructure", words)
