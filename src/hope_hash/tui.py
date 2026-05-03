"""Curses-дашборд для майнера.

Использует stdlib ``curses``. На Windows curses не входит в стандартную
поставку CPython — нужен пакет ``windows-curses``. Мы НЕ добавляем его
в зависимости (CLAUDE.md: pure stdlib), а graceful-fail с понятным
сообщением, если импорт не удался.

Архитектура:

- ``StatsProvider`` — лёгкая шина данных. Майнер пушит сюда снапшоты,
  TUI и /healthz/web-дашборд читают. Один источник правды для всех
  внешних потребителей.
- ``TUIApp`` — curses-цикл в фоновой нити, перерисовывает экран каждый
  ``refresh_interval`` секунд. Quit на ``q`` или ``Ctrl+C``.

Связка с ``mine()``: при старте CLI делает ``provider = StatsProvider()``,
отдаёт его в ``mine()`` (через новый kwarg) и одновременно стартует
``TUIApp(provider).start()``. mine() в hot-path вызывает
``provider.update(...)`` — это thread-safe и дешёвое присваивание.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ─────────────────── Stats provider (без curses) ───────────────────


@dataclass
class StatsSnapshot:
    """Иммутабельный снапшот статистики майнера для одного кадра дашборда."""

    started_at: float = field(default_factory=time.time)
    hashrate_ema: float = 0.0
    hashrate_last: float = 0.0
    workers: int = 0
    pool_difficulty: float = 0.0
    current_job_id: Optional[str] = None
    shares_total: int = 0
    shares_accepted: int = 0
    shares_rejected: int = 0
    last_share_ts: Optional[float] = None
    pool_url: str = ""

    @property
    def uptime_s(self) -> float:
        return max(0.0, time.time() - self.started_at)


class StatsProvider:
    """Потокобезопасный снимок состояния майнера.

    Майнер пушит обновления через ``update_*`` методы; потребители
    (TUI, /healthz, /api/stats в PR C) читают через ``snapshot()``.

    Намеренно plain-data, никакой бизнес-логики — она в ``mine()``.
    """

    def __init__(self, pool_url: str = "", sha_backend: str = "hashlib") -> None:
        self._lock = threading.Lock()
        self._snap = StatsSnapshot(pool_url=pool_url)
        # SHA-backend имя; не часть StatsSnapshot, потому что не меняется в runtime,
        # но web-дашборду удобно читать из одного места.
        self._sha_backend = sha_backend
        # Подписчики на события (share found/accepted/rejected, job change, pool rotation).
        # Каждый callback — sync-функция (event_type: str, payload: dict). Вызывается
        # из публикующей нити (mine/supervisor) под локом списка, поэтому
        # обработчики ДОЛЖНЫ быть быстрыми и не блокировать (например, кладут в Queue).
        self._subscribers: list[Callable[[str, dict[str, Any]], None]] = []
        self._sub_lock = threading.Lock()

    def snapshot(self) -> StatsSnapshot:
        """Атомарный снимок текущего состояния."""
        with self._lock:
            # dataclasses.replace копирует — потребитель не увидит мутаций.
            return StatsSnapshot(
                started_at=self._snap.started_at,
                hashrate_ema=self._snap.hashrate_ema,
                hashrate_last=self._snap.hashrate_last,
                workers=self._snap.workers,
                pool_difficulty=self._snap.pool_difficulty,
                current_job_id=self._snap.current_job_id,
                shares_total=self._snap.shares_total,
                shares_accepted=self._snap.shares_accepted,
                shares_rejected=self._snap.shares_rejected,
                last_share_ts=self._snap.last_share_ts,
                pool_url=self._snap.pool_url,
            )

    def update_hashrate(self, ema: float, last_sample: float, workers: int) -> None:
        with self._lock:
            self._snap.hashrate_ema = float(ema)
            self._snap.hashrate_last = float(last_sample)
            self._snap.workers = int(workers)

    def update_job(self, job_id: Optional[str], pool_difficulty: float) -> None:
        with self._lock:
            prev = self._snap.current_job_id
            self._snap.current_job_id = job_id
            self._snap.pool_difficulty = float(pool_difficulty)
        # Событие job-change только при реальной смене (чтобы не флудить SSE
        # на каждый пересчёт хешрейта).
        if prev != job_id:
            self._publish(
                "job", {"job_id": job_id, "pool_difficulty": float(pool_difficulty)}
            )

    def record_share(self, accepted: Optional[bool] = None) -> None:
        """Учёт шар. accepted=None → submitted (ещё ждём ответа пула).
        accepted=True/False → ответ пришёл."""
        with self._lock:
            if accepted is None:
                self._snap.shares_total += 1
                self._snap.last_share_ts = time.time()
                event = "share_found"
            elif accepted:
                self._snap.shares_accepted += 1
                event = "share_accepted"
            else:
                self._snap.shares_rejected += 1
                event = "share_rejected"
        self._publish(event, {"accepted": accepted})

    def update_pool(self, pool_url: str) -> None:
        """Меняет текущий pool URL (для multi-pool failover).

        TUI читает это поле каждый кадр — обновление видно сразу
        после ротации в supervisor_loop.
        """
        with self._lock:
            self._snap.pool_url = str(pool_url)
        self._publish("pool", {"pool_url": pool_url})

    @property
    def sha_backend(self) -> str:
        """Имя текущего SHA-backend (для /api/stats и UI)."""
        return self._sha_backend

    def set_sha_backend(self, name: str) -> None:
        """Меняет SHA-backend имя (вызывается один раз из cli.main)."""
        self._sha_backend = str(name)

    # ─── publish/subscribe для SSE (web дашборд) ───

    def subscribe(
        self, callback: Callable[[str, dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Подписка на события майнера.

        Возвращает функцию-отписку. Callback дёргается синхронно из
        публикующей нити, поэтому должен быть мгновенным (в идеале —
        ``queue.put_nowait``). Любые исключения внутри callback ловятся
        и логируются как warning, чтобы один сломанный подписчик не
        ронял publish для остальных.
        """
        with self._sub_lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._sub_lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    # Уже удалён — идемпотентно
                    pass

        return _unsubscribe

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Внутренний publish — рассылает событие всем подписчикам.

        Снимок списка под локом, потом вызовы без удержания лока, чтобы
        подписчик мог при желании отписаться внутри своего callback.
        """
        with self._sub_lock:
            subs = list(self._subscribers)
        if not subs:
            return
        for cb in subs:
            try:
                cb(event_type, payload)
            except Exception as exc:  # noqa: BLE001 — пользовательский callback
                logging.getLogger("hope_hash").warning(
                    "[stats] подписчик упал на событии %s: %s", event_type, exc
                )

    def publish_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Публикует произвольное событие (используется mine() для
        share-found / share-accepted / share-rejected / job-change).

        Публичный alias для ``_publish``, чтобы внешний код не лез в
        приватный API. Имя события — строка, payload — JSON-сериализуемый
        dict.
        """
        self._publish(event_type, payload)


def format_rate(rate: float) -> str:
    """Человекочитаемый хешрейт. Копия из miner._format_rate (без импорта)."""
    if rate < 1000:
        return f"{rate:.0f} H/s"
    if rate < 1_000_000:
        return f"{rate / 1000:.2f} KH/s"
    return f"{rate / 1_000_000:.2f} MH/s"


def format_uptime(s: float) -> str:
    """Аптайм в формате HH:MM:SS (с днями при >24ч)."""
    s_int = int(s)
    days, rem = divmod(s_int, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# ─────────────────── Curses-приложение ───────────────────


def is_curses_available() -> bool:
    """True если ``curses`` импортируется в текущем окружении."""
    try:
        import curses  # noqa: F401
        return True
    except ImportError:
        return False


class TUIApp:
    """Фоновый curses-дашборд. Старт/стоп идемпотентны.

    Если curses недоступен (Windows без windows-curses), ``start()``
    логирует warning и возвращается без поднятия нити — майнер
    продолжает работать. Это сознательный degrade, не падение.
    """

    def __init__(
        self,
        provider: StatsProvider,
        stop_event: threading.Event,
        refresh_interval: float = 1.0,
    ) -> None:
        self.provider = provider
        self.stop_event = stop_event
        self.refresh_interval = refresh_interval
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()

    def start(self) -> bool:
        """Запускает curses в фоне. Возвращает True если поднялся."""
        # Импортируем здесь, чтобы модуль загружался даже без curses.
        try:
            import curses  # noqa: F401
        except ImportError:
            import logging
            logging.getLogger("hope_hash").warning(
                "[tui] curses недоступен (на Windows нужен пакет windows-curses); "
                "TUI выключен, майнер работает без дашборда"
            )
            return False

        with self._lifecycle_lock:
            if self._thread is not None:
                return True
            self._thread = threading.Thread(
                target=self._run,
                name="hope_hash-tui",
                daemon=True,
            )
            self._thread.start()
        return True

    def stop(self, timeout: float = 2.0) -> None:
        """Просит TUI остановиться. Идемпотентно."""
        with self._lifecycle_lock:
            t = self._thread
            self._thread = None
        # stop_event общий с майнером — устанавливать его TUI не должен,
        # просто ждём пока сам выйдет (он смотрит на stop_event каждый кадр).
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    # ─── внутренние методы ───

    def _run(self) -> None:
        import curses

        try:
            curses.wrapper(self._loop)
        except Exception as exc:
            # Не валим майнер из-за сбоя в дашборде — терминал странный,
            # маленький экран, что угодно. Лучше degrade в логи.
            import logging
            logging.getLogger("hope_hash").warning(
                "[tui] curses-цикл упал: %s — TUI отключён", exc
            )

    def _loop(self, stdscr) -> None:  # noqa: ANN001 — curses screen object
        import curses

        curses.curs_set(0)
        stdscr.nodelay(True)  # getch не блокирует — без него Ctrl+C ловится плохо
        stdscr.timeout(int(self.refresh_interval * 1000))

        while not self.stop_event.is_set():
            try:
                self._draw(stdscr)
            except curses.error:
                # Резайз окна, обрезание строки за пределы экрана —
                # не повод падать, перерисуем на следующем кадре.
                pass

            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q"), 27):  # 27 == ESC
                self.stop_event.set()
                break

    def _draw(self, stdscr) -> None:  # noqa: ANN001
        import curses

        snap = self.provider.snapshot()
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        title = " Hope-Hash · solo BTC miner "
        try:
            stdscr.addstr(0, 0, title.center(w - 1, "─"), curses.A_BOLD)
        except curses.error:
            pass

        rows: list[tuple[str, str]] = [
            ("Pool", snap.pool_url or "(disconnected)"),
            ("Uptime", format_uptime(snap.uptime_s)),
            ("Hashrate (EMA)", format_rate(snap.hashrate_ema)),
            ("Hashrate (last)", format_rate(snap.hashrate_last)),
            ("Workers", str(snap.workers)),
            ("Pool difficulty", f"{snap.pool_difficulty}"),
            ("Job ID", _truncate(snap.current_job_id or "—", 24)),
            ("Shares submitted", str(snap.shares_total)),
            ("Shares accepted", str(snap.shares_accepted)),
            ("Shares rejected", str(snap.shares_rejected)),
            ("Last share", _format_ago(snap.last_share_ts)),
        ]

        for i, (label, value) in enumerate(rows, start=2):
            if i >= h - 2:
                break
            line = f"  {label:<18} {value}"
            try:
                stdscr.addstr(i, 0, line[: w - 1])
            except curses.error:
                pass

        footer = " press q or ESC to quit · refresh 1s "
        if h > 2:
            try:
                stdscr.addstr(h - 1, 0, footer.ljust(w - 1, " "), curses.A_DIM)
            except curses.error:
                pass

        stdscr.refresh()


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _format_ago(ts: Optional[float]) -> str:
    if ts is None:
        return "never"
    delta = max(0.0, time.time() - ts)
    if delta < 60:
        return f"{delta:.0f}s ago"
    if delta < 3600:
        return f"{delta / 60:.1f}m ago"
    return f"{delta / 3600:.1f}h ago"
