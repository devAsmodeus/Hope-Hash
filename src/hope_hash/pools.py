"""Список пулов с round-robin failover.

Зачем отдельный модуль: supervisor_loop в miner.py не должен знать
про набор пулов — это конфигурация, а не runtime-логика. ``PoolList``
держит состояние (текущий индекс, счётчики провалов) и решает, когда
ротировать. supervisor получает «текущий хост» из ``current()`` и
сообщает обратно через ``mark_failed()`` / ``mark_success()``.

Поведение:

- ``current()`` всегда возвращает текущий ``(host, port)``.
- ``mark_failed()`` инкрементирует счётчик провалов для текущего пула.
  При достижении ``rotate_after_failures`` ротирует на следующий и
  сбрасывает локальный счётчик.
- ``mark_success()`` сбрасывает счётчик провалов на текущем пуле — после
  успешного коннекта мы доверяем ему снова.
- ``rotate()`` — ручной форс-ротейт без условия (используется тестами и
  для обработки явного «отключение по времени», а не «не удалось коннект»).
- ``full_cycle_failed()`` — True, если за последний раунд все пулы
  отметились как failed. Supervisor использует это, чтобы понять, что
  пора применить exponential backoff (а не сразу ретраить).
"""

from __future__ import annotations

import threading
from typing import Iterable


PoolEndpoint = tuple[str, int]


class PoolList:
    """Round-robin список пулов с подсчётом провалов.

    Все методы потокобезопасны (используются из supervisor-нити, но
    могут читаться из cli/healthz). Локально используем ``RLock``, чтобы
    публичные методы могли вызывать друг друга без deadlock.
    """

    def __init__(
        self,
        endpoints: Iterable[PoolEndpoint],
        rotate_after_failures: int = 3,
    ) -> None:
        eps: list[PoolEndpoint] = list(endpoints)
        if not eps:
            raise ValueError("PoolList требует хотя бы один endpoint")
        if rotate_after_failures < 1:
            raise ValueError("rotate_after_failures должно быть >= 1")
        # Дедуп с сохранением порядка: пользователь мог по ошибке указать
        # один и тот же пул дважды; ротация по дублям бессмысленна.
        seen: set[PoolEndpoint] = set()
        deduped: list[PoolEndpoint] = []
        for host, port in eps:
            key = (host.lower(), int(port))
            if key in seen:
                continue
            seen.add(key)
            deduped.append((host, int(port)))

        self._endpoints: list[PoolEndpoint] = deduped
        self._rotate_after = int(rotate_after_failures)
        self._lock = threading.RLock()
        self._idx = 0
        # Счётчики провалов на каждый endpoint (не локальный для current,
        # а глобальный — чтобы full_cycle_failed было считаемо).
        self._failures: list[int] = [0] * len(self._endpoints)
        # Сколько раз ротировали с момента последнего успешного коннекта.
        # Если ротировали >= len(endpoints), значит обошли весь круг,
        # никто не поднялся → full_cycle_failed.
        self._rotations_since_success = 0

    # ─────────────── чтение ───────────────

    def current(self) -> PoolEndpoint:
        """Текущий ``(host, port)``."""
        with self._lock:
            return self._endpoints[self._idx]

    def current_url(self) -> str:
        """Удобный формат для логов и TUI: ``host:port``."""
        host, port = self.current()
        return f"{host}:{port}"

    @property
    def size(self) -> int:
        return len(self._endpoints)

    def all_endpoints(self) -> list[PoolEndpoint]:
        """Копия списка — для отладки и тестов."""
        with self._lock:
            return list(self._endpoints)

    def failures(self, idx: int | None = None) -> int:
        """Счётчик провалов для индекса (по умолчанию — текущий)."""
        with self._lock:
            i = self._idx if idx is None else idx
            return self._failures[i]

    def full_cycle_failed(self) -> bool:
        """True если за последний раунд все пулы упали без единого успеха."""
        with self._lock:
            return self._rotations_since_success >= len(self._endpoints)

    # ─────────────── мутации ───────────────

    def mark_failed(self) -> bool:
        """Засчитывает провал текущему пулу. Возвращает True если ротировали."""
        with self._lock:
            self._failures[self._idx] += 1
            if self._failures[self._idx] >= self._rotate_after:
                self._rotate_locked()
                return True
            return False

    def mark_success(self) -> None:
        """Текущий пул успешно коннектнулся — сбрасываем его счётчик и
        раундовый аккумулятор. Остальные счётчики не трогаем: они
        важны как «история», но именно текущий мы только что подтвердили.
        """
        with self._lock:
            self._failures[self._idx] = 0
            self._rotations_since_success = 0

    def rotate(self) -> PoolEndpoint:
        """Принудительный round-robin сдвиг. Возвращает новый current."""
        with self._lock:
            self._rotate_locked()
            return self._endpoints[self._idx]

    def reset_round(self) -> None:
        """Сбрасывает аккумулятор full_cycle_failed без сдвига индекса.

        Используется после exponential-backoff паузы: «мы подождали,
        давайте ещё раз попробуем все пулы по очереди».
        """
        with self._lock:
            self._rotations_since_success = 0

    # ─────────────── внутренние ───────────────

    def _rotate_locked(self) -> None:
        """Ротация под уже взятым ``self._lock``."""
        self._idx = (self._idx + 1) % len(self._endpoints)
        # Счётчик провалов следующего пула не сбрасываем: если он недавно
        # упал — пусть это видно в .failures(). Но локальный аккумулятор
        # ротаций бьём, чтобы full_cycle_failed считался корректно.
        self._rotations_since_success += 1


def parse_pool_spec(spec: str, default_port: int = 3333) -> PoolEndpoint:
    """Парсит строку ``host:port`` или ``host`` в endpoint.

    Принимает оба варианта, чтобы пользователь мог писать ``--pool host``,
    если у него стандартный порт. Падает на пустой строке или
    некорректном порте — это явный signal-ошибки в CLI, а не silent default.
    """
    s = (spec or "").strip()
    if not s:
        raise ValueError("пустой pool-спецификатор")
    if ":" in s:
        host, _, port_s = s.rpartition(":")
        host = host.strip()
        if not host:
            raise ValueError(f"пустой host в '{spec}'")
        try:
            port = int(port_s)
        except ValueError as e:
            raise ValueError(f"некорректный порт в '{spec}': {port_s}") from e
        if not (0 < port < 65536):
            raise ValueError(f"порт вне диапазона 1..65535: {port}")
        return host, port
    return s, int(default_port)
