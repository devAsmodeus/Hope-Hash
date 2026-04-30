"""Настройка единого logger пакета hope_hash (приватный модуль)."""

import logging


# Единый logger для всех модульных «тэгов» ([net]/[stratum]/[mine]/[stats]/[main]).
# Тэги остаются частью message — это часть стиля проекта, по нему удобно фильтровать grep'ом.
# Имя совпадает с именем пакета: так стандартно настраивается фильтрация в logging.
logger = logging.getLogger("hope_hash")


def setup_logging(level: int = logging.INFO) -> None:
    """
    Конфигурирует корневой logging формат один раз.

    Вызывается из cli.main(). Идемпотентность basicConfig: повторный вызов
    в том же процессе ничего не сломает, но и не перенастроит — это ок.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
