"""Точка входа CLI: argparse, запуск supervisor + mine() + observers."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

from ._logging import logger, setup_logging
from .address import validate_btc_address
from .banner import print_banner
from .metrics import Metrics, MetricsServer, build_health_snapshot
from .miner import mine, supervisor_loop
from .notifier import TelegramNotifier
from .storage import ShareStore
from .stratum import StratumClient
from .tui import StatsProvider, TUIApp, format_rate, format_uptime, is_curses_available


POOL_HOST = "solo.ckpool.org"
POOL_PORT = 3333


def _default_workers() -> int:
    """Один CPU оставляем сетевой части/IO. Минимум — 1 воркер."""
    cpu = os.cpu_count() or 1
    return max(1, cpu - 1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hope_hash",
        description="Учебный solo BTC miner на чистом stdlib.",
    )
    parser.add_argument("btc_address", nargs="?", default=None,
                        help="BTC-адрес для выплат (на него уйдёт награда). "
                             "Не нужен в режиме --demo.")
    parser.add_argument("worker_name", nargs="?", default="py01",
                        help="Имя воркера (по умолчанию: py01).")
    parser.add_argument(
        "--workers", type=int, default=_default_workers(),
        help=f"Число процессов-воркеров (по умолчанию: {_default_workers()} = cpu_count - 1).",
    )
    parser.add_argument(
        "--db", type=str, default="hope_hash.db",
        help="Путь к SQLite-журналу шаров (по умолчанию: hope_hash.db).",
    )
    parser.add_argument(
        "--no-db", action="store_true",
        help="Отключить SQLite-журнал (--db игнорируется).",
    )
    parser.add_argument(
        "--metrics-port", type=int, default=9090,
        help="Порт Prometheus /metrics (по умолчанию: 9090, 0 — отключить).",
    )
    parser.add_argument(
        "--suggest-diff", type=float, default=None,
        metavar="DIFF",
        help="Запросить у пула эту сложность после авторизации (vardiff). "
             "Пример: --suggest-diff 0.001",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Запустить demo-режим без подключения к пулу: "
             "ищет nonce для синтетического блока с низкой сложностью.",
    )
    parser.add_argument(
        "--demo-diff", type=float, default=0.001,
        metavar="DIFF",
        help="Сложность для demo-режима (по умолчанию: 0.001).",
    )
    parser.add_argument(
        "--benchmark", action="store_true",
        help="Запустить бенчмарк pure-Python хешрейта (без сети, без шар). "
             "Полезно как baseline перед оптимизациями (C/Rust/SIMD/GPU).",
    )
    parser.add_argument(
        "--bench-duration", type=float, default=10.0,
        metavar="SEC",
        help="Длительность бенчмарка в секундах (по умолчанию: 10).",
    )
    parser.add_argument(
        "--tui", action="store_true",
        help="Включить curses-дашборд (на Windows нужен windows-curses; "
             "при отсутствии — graceful skip с логом).",
    )
    parser.add_argument(
        "--no-banner", action="store_true",
        help="Не печатать ASCII-баннер при старте (для systemd/cron-режима).",
    )
    parser.add_argument(
        "--log-file", type=str, default=None,
        metavar="PATH",
        help="Дублировать лог в файл. Полезно вместе с --tui, "
             "когда stdout занят дашбордом.",
    )
    parser.add_argument(
        "--healthz-stale-after", type=float, default=600.0,
        metavar="SEC",
        help="Сколько секунд без шар до того, как /healthz отдаёт degraded "
             "(по умолчанию: 600).",
    )
    return parser.parse_args()


def _setup_logging_for_tui(log_file: str | None, tui_active: bool) -> None:
    """Логи + curses несовместимы: stdout-handler рвёт перерисовку. Если TUI
    включён — поднимаем уровень до WARNING на консоли, а INFO направляем в
    файл (если задан --log-file). Без TUI — поведение прежнее, basicConfig.
    """
    if not tui_active:
        setup_logging()
        if log_file:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logging.getLogger().addHandler(fh)
        return

    # TUI режим: убираем default handlers basicConfig, добавляем тихий console
    # на WARNING+ и файл (если задан) на INFO.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.INFO)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root.addHandler(console)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        root.addHandler(fh)


def _format_stats_message(snap) -> str:
    """Сборка ответа на /stats для Telegram."""
    return (
        "📊 Hope-Hash stats\n"
        f"uptime: {format_uptime(snap.uptime_s)}\n"
        f"hashrate (EMA): {format_rate(snap.hashrate_ema)}\n"
        f"workers: {snap.workers}\n"
        f"pool diff: {snap.pool_difficulty}\n"
        f"shares: {snap.shares_total} sent / "
        f"{snap.shares_accepted} ok / {snap.shares_rejected} rej\n"
        f"job: {snap.current_job_id or '—'}"
    )


def main():
    # Защитный вызов: на Windows multiprocessing требует freeze_support()
    # при запуске через `python -m hope_hash`. Без него spawn-дети могут
    # пытаться повторно стартовать main() и упасть.
    multiprocessing.freeze_support()

    args = _parse_args()
    n_workers = max(1, args.workers)

    # TUI работает только в реальном майнинге; для bench/demo просто игнорируем.
    tui_requested = bool(getattr(args, "tui", False)) and not (args.benchmark or args.demo)
    tui_active = tui_requested and is_curses_available()
    if tui_requested and not tui_active:
        # Лог через basicConfig (он ниже в setup), а пока просто print —
        # пользователь должен это увидеть до тишины TUI-режима.
        print(
            "warning: --tui запрошен, но curses недоступен в этом Python "
            "(на Windows нужен пакет windows-curses). Майнер продолжит без TUI.",
            file=sys.stderr,
        )

    _setup_logging_for_tui(args.log_file, tui_active)

    if not args.no_banner and not tui_active:
        # В TUI-режиме баннер ломает curses-кадр; пропускаем.
        print_banner()

    if args.benchmark and args.demo:
        print("error: --benchmark и --demo взаимоисключающи", file=sys.stderr)
        sys.exit(2)

    if args.benchmark:
        from .bench import run_benchmark
        run_benchmark(duration_s=args.bench_duration, n_workers=n_workers)
        return

    if args.demo:
        from .demo import run_demo
        run_demo(n_workers=n_workers, diff=args.demo_diff)
        return

    if not args.btc_address:
        print("error: btc_address обязателен (или используйте --demo)", file=sys.stderr)
        sys.exit(2)

    # Pre-flight: ловим опечатки/невалидный формат локально, до соединения с пулом.
    # Без этого пул отклоняет mining.authorize, и пользователь видит мутное
    # "auth failed" вместо конкретной причины.
    try:
        validate_btc_address(args.btc_address)
    except ValueError as e:
        print(f"error: некорректный BTC-адрес '{args.btc_address}': {e}", file=sys.stderr)
        sys.exit(2)

    # ─── observers ───
    # Все три опциональны и не зависят друг от друга. Каждый сам решает,
    # включаться ли (notifier — по env vars; metrics — по порту; store — по флагу).
    store: ShareStore | None = None
    if not args.no_db:
        store = ShareStore(Path(args.db))

    metrics: Metrics | None = None
    metrics_server: MetricsServer | None = None
    if args.metrics_port > 0:
        metrics = Metrics()
        metrics_server = MetricsServer(metrics, port=args.metrics_port)
        metrics_server.start()

    notifier = TelegramNotifier.from_env()
    notifier.notify_started(args.btc_address, args.worker_name)

    if store is not None:
        session_id = store.start_session(POOL_HOST, args.btc_address, args.worker_name)
    else:
        session_id = None

    # ─── stats provider, TUI и healthz ───
    pool_url = f"{POOL_HOST}:{POOL_PORT}"
    stats_provider = StatsProvider(pool_url=pool_url)

    # ─── сетевая часть и mine() ───
    stop = threading.Event()
    restart_event = threading.Event()
    client = StratumClient(POOL_HOST, POOL_PORT, args.btc_address, args.worker_name,
                           stop_event=stop, suggest_diff=args.suggest_diff)

    # Сетевая часть живёт в отдельной нити-супервизоре: она держит коннект,
    # переподключается при разрывах и сама поднимает reader_loop. main thread
    # отдан под mine(), чтобы Ctrl+C ловился предсказуемо.
    supervisor = threading.Thread(target=supervisor_loop, args=(client, restart_event),
                                  name="stratum-supervisor", daemon=False)
    supervisor.start()

    # Healthz: знаем, что reader жив, если supervisor поднял текущий коннект.
    # Свежий timestamp хешрейта храним сами через wrap-callable.
    started_at = time.time()
    last_hashrate_ts: dict[str, float | None] = {"ts": None}

    def _bump_hashrate_ts() -> None:
        last_hashrate_ts["ts"] = time.time()

    if metrics_server is not None:
        def _health_provider() -> dict:
            snap = stats_provider.snapshot()
            # reader_alive: считаем sock != None как «коннект жив» — это
            # не идеально (между connect и subscribe он уже не None),
            # но достаточно для liveness-зонда.
            reader_alive = client.sock is not None and supervisor.is_alive()
            return build_health_snapshot(
                reader_alive=reader_alive,
                hashrate_ema=snap.hashrate_ema,
                hashrate_ts=last_hashrate_ts["ts"],
                last_share_ts=snap.last_share_ts,
                started_at=started_at,
                stale_after_s=args.healthz_stale_after,
            )
        metrics_server.set_health_provider(_health_provider)

    # TUI поднимаем сразу — он покажет «жду первый job».
    tui_app: TUIApp | None = None
    if tui_active:
        tui_app = TUIApp(stats_provider, stop_event=stop)
        tui_app.start()

    # ─── Telegram inbound (опционально) ───
    if notifier.enabled and TelegramNotifier.inbound_enabled_in_env():
        def _on_stats() -> str:
            return _format_stats_message(stats_provider.snapshot())

        def _on_stop() -> str:
            logger.info("[tg] /stop принят, выставляю stop_event")
            stop.set()
            client.close()
            return "🛑 stop_event установлен, майнер останавливается"

        def _on_restart() -> str:
            logger.info("[tg] /restart принят, выставляю restart_event")
            restart_event.set()
            client.close()
            return "♻️ restart-сигнал отправлен"

        def _on_help() -> str:
            return "Доступные команды: /stats /stop /restart /help"

        notifier.register_command("/stats", _on_stats)
        notifier.register_command("/stop", _on_stop)
        notifier.register_command("/restart", _on_restart)
        notifier.register_command("/help", _on_help)
        notifier.register_command("/start", _on_help)
        notifier.start_inbound()

    logger.info(f"[main] жду первый job от пула... (воркеров: {n_workers})")
    while client.current_job is None and not stop.is_set():
        time.sleep(0.1)

    # Оборачиваем mine() так, чтобы успевать обновлять last_hashrate_ts:
    # внутри mine() уже идут update_hashrate-ы, но timestamp нужен снаружи
    # (для healthz). Делаем это через monkey-патч update_hashrate.
    _orig_update = stats_provider.update_hashrate

    def _wrapped_update(ema: float, last_sample: float, workers: int) -> None:
        _orig_update(ema, last_sample, workers)
        _bump_hashrate_ts()

    stats_provider.update_hashrate = _wrapped_update  # type: ignore[method-assign]

    try:
        if not stop.is_set():
            mine(client, stop, n_workers=n_workers,
                 store=store, metrics=metrics, notifier=notifier,
                 stats_provider=stats_provider)
    except KeyboardInterrupt:
        logger.info("[main] остановка по Ctrl+C")
    finally:
        # Согласованная остановка: флаг → закрытие сокета (recv разблокируется)
        # → join всех нитей. Никаких висячих daemon'ов.
        stop.set()
        client.close()
        if tui_app is not None:
            tui_app.stop()
        supervisor.join(timeout=5)
        if supervisor.is_alive():
            logger.warning("[main] supervisor не остановился за 5с")

        # Закрываем observers последними, чтобы дать им зафиксировать финальные события.
        notifier.notify_stopped()
        notifier.shutdown()
        if metrics_server is not None:
            metrics_server.stop()
        if store is not None:
            if session_id is not None:
                store.end_session(session_id)
            store.close()
