"""Замок одного экземпляра: advisory lock на открытом дескрипторе.

Приём канонический — `flock` на POSIX, байтовый замок `LockFileEx` на Windows.
Замок принадлежит ДЕСКРИПТОРУ, а не файлу, поэтому ядро снимает его ровно в тот
момент, когда дескриптор закрывается, — включая падение, `SIGKILL` и
`TerminateProcess`. Ни срока годности, ни уборки осиротевших замков не нужно
вовсе: «процесс умер» и «замок свободен» — это одно и то же событие.

Чем это лучше PID-файла (а он в проекте был): у PID-файла гонка между «прочитали
владельца» и «записали себя», а переиспользованный номер процесса даёт ложное
«занято». Разбор обеих ловушек: https://rednafi.com/misc/run-single-instance/,
https://bashsnippets.xyz/snippets/bash-flock-single-instance

Форма взята у `tox-dev/filelock` (`_unix.py`, `_windows.py`), сам пакет — нет:
там под Windows `NtCreateFile` через `ctypes` ради случаев, которых здесь не
бывает, а нам хватает `msvcrt` из stdlib.

⚠️ **Расхождение с каноном, и оно намеренное.** `filelock` ждёт освобождения по
таймауту — мы отказываем СРАЗУ, бросая `Busy`. Ждать в процессе незачем: стадии
запускает лаунчер, и занятый замок он догоняет следующим тиком, отвечая кодом 75
(docs/scheduling.md). Ожидание внутри процесса только съело бы `ExecutionTimeLimit`
задачи.

⚠️ **Замок файлом — это замок НА МАШИНЕ.** Ресурсы, которые он стережёт (паузы
источников, одна видеокарта, платные запросы), машиной и ограничены: у второй
машины своя база и свой сервер (docs/storage-engine.md).
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .config import DATA_DIR

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

#: Каталог замков. Рядом с `.last-full-run` и `.run-state.json`, то есть на том
#: же томе, что и остальное состояние (`DATA_DIR` бывает на другом диске).
#: Тесты подменяют его на временный — иначе набор пишет замки в боевой каталог.
LOCK_DIR = DATA_DIR / "locks"

#: Замок висит на байте 0, визитка держателя начинается с байта 1.
#:
#: ⚠️ Разъехаться они обязаны, и причина не косметическая: байтовый замок
#: Windows — МАНДАТОРНЫЙ, и чужой процесс, прочитавший залоченный байт, падает.
#: Визитка же читается именно тогда, когда замок занят.
CARD_AT = 1

#: Имя замка попадает в имя файла, а среди имён есть `backfill:<источники>`:
#: двоеточие в имени файла Windows запрещено, а сами имена источников приходят
#: из `--source`, то есть от пользователя и ДО всякой валидации.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.,-]")

Holder = tuple[str, int, datetime]


class Busy(Exception):
    """Замок держит кто-то другой. `holder` — визитка, если её удалось прочесть."""

    def __init__(self, name: str, holder: Holder | None) -> None:
        self.name = name
        self.holder = holder
        super().__init__(f"замок {name} занят")


def path(name: str) -> Path:
    return LOCK_DIR / (_UNSAFE.sub("_", name) + ".lock")


def _acquire(fd: int) -> bool:
    """Неблокирующий эксклюзивный замок на байте 0. `False` — занят."""
    try:
        if sys.platform == "win32":
            # ⚠️ Диапазон считается ОТ ТЕКУЩЕЙ ПОЗИЦИИ, а она сразу после
            # `os.open` равна нулю. Отсюда же `lseek(0)` перед снятием.
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _release(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _put_card(fd: int) -> None:
    card = f"{socket.gethostname()} {os.getpid()} {datetime.now(UTC).isoformat()}\n"
    os.lseek(fd, CARD_AT, os.SEEK_SET)
    os.write(fd, card.encode("utf-8"))
    # ⚠️ Хвост прошлого держателя НЕ обрезаем: `ftruncate` поверх залоченного
    # байта — лишний риск на ровном месте, а визитка читается до первого
    # перевода строки и остатка не видит.


def read_card(name: str) -> Holder | None:
    try:
        with open(path(name), "rb") as f:
            f.seek(CARD_AT)
            host, _, rest = f.readline().decode("utf-8", "replace").strip().partition(" ")
            pid, _, when = rest.partition(" ")
            return host, int(pid), datetime.fromisoformat(when)
    except (OSError, ValueError):
        return None


def probe(name: str) -> tuple[bool, Holder | None]:
    """(занят ли замок, визитка держателя) — не беря его и не оставляя своей визитки.

    ⚠️ Одна визитка ничего не говорит: она остаётся в файле и после того, как
    держатель ушёл. Занятость решает только попытка взять замок.
    """
    target = path(name)
    if not target.exists():
        return False, None
    fd = os.open(target, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        if _acquire(fd):
            _release(fd)
            return False, None
    finally:
        os.close(fd)
    return True, read_card(name)


@contextlib.contextmanager
def hold(name: str) -> Iterator[None]:
    """Держать замок `name`, пока идёт работа. Занят — сразу `Busy`.

    ⚠️ Файл замка не удаляется НИКОГДА — ни на выходе, ни уборкой. Удаление это
    известная гонка: второй процесс открыл файл, первый его удалил, третий создал
    новый — и два процесса держат замки на разных inode, считая их одним. Пустые
    файлы в каталоге замков — это норма, а не мусор (так же поступает `filelock`).
    """
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    if not _acquire(fd):
        os.close(fd)
        raise Busy(name, read_card(name))
    try:
        _put_card(fd)
        yield
    finally:
        with contextlib.suppress(OSError):
            _release(fd)
        os.close(fd)
