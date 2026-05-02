"""ASCII-art баннер для шапки CLI.

Выводится один раз при старте через ``print_banner()``. Гасится флагом
``--no-banner`` для log-only режима (cron/systemd/контейнер с
агрегатором логов).

Дизайн: компактный (≤10 строк), ASCII-only — не ломает терминалы без
UTF-8/эмоджи. Печатается через ``sys.stdout`` напрямую, а не через
logger: баннер — это UX, а не лог-событие.
"""

from __future__ import annotations

import sys
from typing import TextIO

from . import __version__


# 5-строчный логотип "HOPE HASH" собранный вручную через | / _ / -.
# Не используем pyfiglet/figlet, чтобы не тащить зависимость.
_BANNER_LINES: tuple[str, ...] = (
    r" _   _  ___  ____  _____   _   _    _    ____  _   _ ",
    r"| | | |/ _ \|  _ \| ____| | | | |  / \  / ___|| | | |",
    r"| |_| | | | | |_) |  _|   | |_| | / _ \ \___ \| |_| |",
    r"|  _  | |_| |  __/| |___  |  _  |/ ___ \ ___) |  _  |",
    r"|_| |_|\___/|_|   |_____| |_| |_/_/   \_\____/|_| |_|",
)


def render_banner(version: str = __version__) -> str:
    """Возвращает баннер строкой. Удобно для тестов и для записи в файл-лог."""
    # ASCII-only: dot-separator вместо middle-dot — иначе ломается на консолях
    # без UTF-8 (старые Win-консоли, syslog-агрегаторы с локалью C).
    subtitle = f"  solo BTC miner on pure stdlib -- v{version}"
    return "\n".join(_BANNER_LINES) + "\n" + subtitle + "\n"


def print_banner(stream: TextIO | None = None) -> None:
    """Печатает баннер в stdout (или в указанный stream)."""
    out = stream if stream is not None else sys.stdout
    out.write(render_banner())
    out.flush()
