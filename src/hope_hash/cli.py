"""Точка входа CLI: argparse, запуск supervisor + mine() + observers."""

import argparse
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

from ._logging import logger, setup_logging
from .address import validate_btc_address
from .metrics import Metrics, MetricsServer
from .miner import mine, supervisor_loop
from .notifier import TelegramNotifier
from .storage import ShareStore
from .stratum import StratumClient


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
    return parser.parse_args()


def main():
    # Защитный вызов: на Windows multiprocessing требует freeze_support()
    # при запуске через `python -m hope_hash`. Без него spawn-дети могут
    # пытаться повторно стартовать main() и упасть.
    multiprocessing.freeze_support()

    setup_logging()
    args = _parse_args()
    n_workers = max(1, args.workers)

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

    # ─── сетевая часть и mine() ───
    stop = threading.Event()
    client = StratumClient(POOL_HOST, POOL_PORT, args.btc_address, args.worker_name,
                           stop_event=stop, suggest_diff=args.suggest_diff)

    # Сетевая часть живёт в отдельной нити-супервизоре: она держит коннект,
    # переподключается при разрывах и сама поднимает reader_loop. main thread
    # отдан под mine(), чтобы Ctrl+C ловился предсказуемо.
    supervisor = threading.Thread(target=supervisor_loop, args=(client,),
                                  name="stratum-supervisor", daemon=False)
    supervisor.start()

    logger.info(f"[main] жду первый job от пула... (воркеров: {n_workers})")
    while client.current_job is None and not stop.is_set():
        time.sleep(0.1)

    try:
        if not stop.is_set():
            mine(client, stop, n_workers=n_workers,
                 store=store, metrics=metrics, notifier=notifier)
    except KeyboardInterrupt:
        logger.info("[main] остановка по Ctrl+C")
    finally:
        # Согласованная остановка: флаг → закрытие сокета (recv разблокируется)
        # → join всех нитей. Никаких висячих daemon'ов.
        stop.set()
        client.close()
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
