"""Имя отчёта при импорте с диска — по BR-45 (без базы)."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_reports.py"
spec = importlib.util.spec_from_file_location("import_reports", SCRIPT)
imp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(imp)

STARTED = datetime(2026, 10, 1, 9, 5, 7, tzinfo=UTC)


def test_name_time_is_utc_whatever_the_host_zone():
    # BR-45: на стенде UTC+6 имя по часам хоста уходило от `started` на 6 ч.
    local = datetime(2026, 10, 1, 15, 5, 7, tzinfo=timezone(timedelta(hours=6)))
    assert imp.report_name("cyber2", local) == "20261001-090507"


def test_rule_names_are_kept() -> None:
    assert imp.report_name("20261001-090507", STARTED) == "20261001-090507"
    assert imp.report_name("tz-kosmos-20261001-090507", STARTED) == "tz-kosmos-20261001-090507"


def test_other_names_become_start_time() -> None:
    assert imp.report_name("cyber2", STARTED) == "20261001-090507"
    assert imp.report_name("tz-kosmos", STARTED) == "20261001-090507"
