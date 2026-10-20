from datetime import date, timedelta

from pelican.weak import asof, tech


class _Store:
    def __init__(self) -> None:
        self.since = None

    def untagged_science(self, model, since, limit=None, ids=None):
        self.since = since
        return []


def test_queue_horizon_follows_asof_cut():
    store = _Store()
    asof.AS_OF = date(2017, 12, 1)
    try:
        tech.run_tech(store)
    finally:
        asof.AS_OF = None
    # Работы 2017 года должны попадать в очередь: горизонт — от среза, а не от часов машины.
    assert store.since.date() == date(2017, 12, 1) - timedelta(days=tech.QUEUE_DAYS)


def test_queue_horizon_without_cut_is_today():
    store = _Store()
    tech.run_tech(store)
    assert store.since.date() == date.today() - timedelta(days=tech.QUEUE_DAYS)


def test_year_only_date_is_cut_by_asof():
    """КиберЛенинка отдаёт дату годом: 2025 под срезом 2020 — из будущего (PEL-H-04)."""
    asof.AS_OF = date(2020, 9, 15)
    try:
        assert asof.too_late("2025")
        assert not asof.too_late("2020")
        assert not asof.too_late("2019")
        assert asof.too_late("2020-09-16")
        assert not asof.too_late("вчера")
    finally:
        asof.AS_OF = None


def test_found_by_search_has_no_horizon():
    """Найденное поиском размечается целиком, и старые работы тоже (PEL-B-01)."""
    store = _Store()
    store.since = "не вызван"
    tech.run_tech(store, ids=[1, 2])
    assert store.since is None
