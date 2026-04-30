"""Demo-режим: майнинг без подключения к пулу, с искусственно низкой сложностью.

Используется для презентаций и проверки корректности pipeline без реальной сети.
Берёт синтетический 80-байтный block header, ищет nonce с помощью тех же
multiprocessing-воркеров, что и реальный майнинг, и завершается при первой находке.

Запуск:
    hope-hash --demo [--workers N] [--demo-diff DIFF]
"""

import queue
import struct
import time

from ._logging import logger
from .block import difficulty_to_target
from .parallel import start_pool, stop_pool


def run_demo(n_workers: int = 1, diff: float = 0.001) -> bool:
    """
    Один раунд demo-майнинга.

    Параметры:
        n_workers  — число параллельных процессов (как в реальном майнинге).
        diff       — желаемая сложность. 0.001 → ~4 млн хешей в среднем,
                     при ~200 KH/s на воркер ожидание ≈ 5–20с.

    Возвращает True, если nonce найден; False — если все воркеры исчерпали
    своё nonce-пространство без находки (возможно при очень высокой diff).
    """
    # Синтетический 76-байтовый header_base:
    # version(4) + prevhash(32) + merkle_root(32) + ntime(4) + nbits(4)
    ntime_le = struct.pack("<I", int(time.time()))
    header_base = (
        b"\x01\x00\x00\x00" +   # version = 1, LE
        b"\x00" * 32 +           # prevhash = нули (блок-ноль)
        b"\x00" * 32 +           # merkle_root = нули
        ntime_le +               # ntime = текущее время
        b"\xff\xff\x00\x1d"      # nbits = произвольно, target задаём через diff
    )
    target = difficulty_to_target(diff)

    logger.info(
        f"[demo] старт demo-режима: difficulty={diff}, workers={n_workers}"
    )
    logger.info(f"[demo] target = {target:#066x}")
    logger.info("[demo] ищу валидный nonce...")

    processes, found_queue, hashes_counter, mp_stop = start_pool(
        n_workers, header_base, target, "00000000",
    )

    found = False
    try:
        while True:
            try:
                nonce_hex, hash_hex, _ = found_queue.get(timeout=1.0)
                logger.info(
                    f"[demo] *** ШАР НАЙДЕН ***  nonce={nonce_hex}  hash={hash_hex}"
                )
                found = True
                break
            except queue.Empty:
                if not any(p.is_alive() for p in processes):
                    logger.info("[demo] все воркеры завершили поиск — nonce не найден")
                    break
    finally:
        stop_pool(processes, found_queue, mp_stop)

    if not found:
        logger.warning("[demo] nonce не найден в заданном пространстве")
    logger.info("[demo] demo-режим завершён")
    return found
