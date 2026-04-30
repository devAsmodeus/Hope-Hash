"""Точка входа CLI: argparse, запуск supervisor + mine()."""

import argparse
import threading
import time

from ._logging import logger, setup_logging
from .miner import mine, supervisor_loop
from .stratum import StratumClient


POOL_HOST = "solo.ckpool.org"
POOL_PORT = 3333


def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        prog="hope_hash",
        description="Учебный solo BTC miner на чистом stdlib.",
    )
    parser.add_argument("btc_address", help="BTC-адрес для выплат (на него уйдёт награда).")
    parser.add_argument("worker_name", nargs="?", default="py01",
                        help="Имя воркера (по умолчанию: py01).")
    args = parser.parse_args()

    btc_address = args.btc_address
    worker_name = args.worker_name

    stop = threading.Event()
    client = StratumClient(POOL_HOST, POOL_PORT, btc_address, worker_name, stop_event=stop)

    # Сетевая часть живёт в отдельной нити-супервизоре: она держит коннект,
    # переподключается при разрывах и сама поднимает reader_loop. main thread
    # отдан под mine(), чтобы Ctrl+C ловился предсказуемо.
    supervisor = threading.Thread(target=supervisor_loop, args=(client,),
                                  name="stratum-supervisor", daemon=False)
    supervisor.start()

    logger.info("[main] жду первый job от пула...")
    while client.current_job is None and not stop.is_set():
        time.sleep(0.1)

    try:
        if not stop.is_set():
            mine(client, stop)
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
