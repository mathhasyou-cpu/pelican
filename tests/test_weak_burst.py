from collections import Counter

from pelican.weak import burst


def test_phrases_drop_stopword_edges():
    got = burst.phrases("Semi-Supervised Classification with Graph Convolutional Networks")
    assert "graph convolutional" in got
    assert "with graph" not in got  # служебное слово с края
    assert "convolutional networks" not in got  # «networks» — общее слово


def test_score_prefers_growth_over_volume():
    # «machine translation» объёмная, но растёт вместе с корпусом; «graph convolutional» — с нуля.
    recent = Counter({"machine translation": 164, "graph convolutional": 19, "rare thing": 3})
    before = Counter({"machine translation": 126, "graph convolutional": 1})
    got = [b.phrase for b in burst.score(recent, before, 37_000, 55_000)]
    assert got[0] == "graph convolutional"
    assert "rare thing" not in got  # ниже MIN_RECENT


def test_score_drops_flat_topics():
    recent = Counter({"flat topic": 100})
    before = Counter({"flat topic": 150})
    assert burst.score(recent, before, 1000, 1500) == []


def test_score_on_filtered_counts_with_full_sums_matches_full_vocabulary():
    """Знаменатели — по всему словарю окна: угасшая фраза прежнего окна в кэш не попадает,
    но в сумме обязана остаться, иначе z смещены (PEL-D-05)."""
    full_recent = Counter({"graph convolutional": 19, "machine translation": 164})
    full_before = Counter({"graph convolutional": 1, "machine translation": 126, "faded topic": 900})
    want = {b.phrase: b.z for b in burst.score(full_recent, full_before, 37_000, 55_000)}
    filtered_before = Counter({g: full_before[g] for g in full_recent})
    got = burst.score(
        full_recent, filtered_before, 37_000, 55_000,
        sum(full_recent.values()), sum(full_before.values()),
    )  # fmt: skip
    assert {b.phrase: round(b.z, 9) for b in got} == {g: round(z, 9) for g, z in want.items()}
    # Без полных сумм — другое число: ровно та ошибка, которую чинит правка.
    wrong = burst.score(full_recent, filtered_before, 37_000, 55_000)
    assert round(wrong[0].z, 6) != round(want[wrong[0].phrase], 6)


def test_distinct_skips_nested_phrases():
    items = [
        burst.Burst("generative adversarial", 171, 29, 9.0),
        burst.Burst("generative adversarial nets", 15, 4, 5.0),
        burst.Burst("edge computing", 39, 10, 4.0),
    ]
    assert [b.phrase for b in burst._distinct(items, 6)] == ["generative adversarial", "edge computing"]
