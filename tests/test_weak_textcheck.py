from pelican.weak.textcheck import quote_appears_in

PAGE = (
    "CRM для выездного сервиса — AstraCRM. Заказы, расписание, клиенты и аналитика "
    "в одном аккаунте. Тариф «Старт» — 1 990 ₽ в месяц, до 5 сотрудников."
)


def test_verbatim_quote_with_folded_quotes_and_case() -> None:
    assert quote_appears_in('Тариф "старт" — 1 990 ₽ в месяц', PAGE)


def test_small_edit_passes_but_a_changed_number_does_not() -> None:
    assert quote_appears_in("Заказы, расписание, клиенты и аналитика в одном акаунте", PAGE)
    assert not quote_appears_in("Тариф «Старт» — 2 990 ₽ в месяц, до 5 сотрудников", PAGE)


def test_ellipsis_fragments_must_appear_in_order() -> None:
    assert quote_appears_in("CRM для выездного сервиса … до 5 сотрудников", PAGE)
    assert not quote_appears_in("до 5 сотрудников … CRM для выездного сервиса", PAGE)


def test_short_or_absent_quote_is_not_evidence() -> None:
    assert not quote_appears_in("CRM", PAGE)
    assert not quote_appears_in("", PAGE)
    assert not quote_appears_in("Автоматизация работы ИИ-агентов по примеру", PAGE)


def test_quote_must_not_cut_a_number() -> None:
    assert not quote_appears_in("990 ₽ в месяц, до 5 сотрудников", PAGE.replace("1 990", "1990"))
