"""Параллельный перебор nonce через multiprocessing.

Архитектура (см. ROADMAP «Уровень 1 / Производительность»):

- Сетевая часть (StratumClient) остаётся в main process. Worker-процессы
  не ходят в сеть — это убирает гонки на сокете и даёт чистый pickle-able
  интерфейс «header_base + диапазон nonce → найденные шары в очередь».
- Пул воркеров пересоздаётся при каждом новом job (terminate + новый pool).
  Это проще, чем делать persistent workers с IPC очередями для job updates,
  и достаточно для учебного кода.
- Windows-совместимость: `multiprocessing` использует `spawn`, поэтому
  `worker` должен быть top-level функцией без замыканий, а все аргументы —
  pickle-able (bytes, int, multiprocessing.Queue/Value/Event).
"""

import multiprocessing as mp
import struct
import time

from ._logging import logger
from .block import double_sha256


# Каждые столько хешей воркер сверяется со stop_event и инкрементирует
# общий счётчик. Чем чаще — тем дороже из-за блокировки на Lock у Value.
# 16k подобрано так же, как в одно-процессорной версии mine() для проверки job_id.
HASHES_PER_TICK = 1 << 14  # 16384


def worker(
    worker_id: int,
    header_base: bytes,
    target: int,
    nonce_start: int,
    nonce_end: int,
    extranonce2: str,
    found_queue: "mp.Queue",
    hashes_counter: "mp.sharedctypes.Synchronized",
    stop_event: "mp.synchronize.Event",
) -> None:
    """
    Перебирает nonce в диапазоне [nonce_start, nonce_end), считает SHA256d.

    Если хеш ≤ target — кладёт ``(nonce_hex_be, hash_hex_be, extranonce2)``
    в ``found_queue``. Каждые ``HASHES_PER_TICK`` хешей:
      - проверяет ``stop_event`` (рано выйти при смене job / Ctrl+C);
      - инкрементирует ``hashes_counter`` (для расчёта EMA-хешрейта в main).

    Сам ``submit`` делается из main process — здесь только находка.
    Сигнатура полностью pickle-able: bytes/int/str + примитивы mp.
    """
    local_hashes = 0  # копим локально, чтобы реже дёргать Lock на Value
    nonce = nonce_start
    try:
        while nonce < nonce_end:
            # Хвост блока: 4 байта nonce, LE.
            header = header_base + struct.pack("<I", nonce)
            h = double_sha256(header)

            # Bitcoin сравнивает хеш как big-endian число — реверсим байты.
            h_int = int.from_bytes(h[::-1], "big")
            if h_int <= target:
                nonce_hex = struct.pack(">I", nonce).hex()
                hash_hex = h[::-1].hex()
                # put не блокирует процесс надолго: очередь почти всегда пуста.
                found_queue.put((nonce_hex, hash_hex, extranonce2))

            nonce += 1
            local_hashes += 1

            if local_hashes >= HASHES_PER_TICK:
                # Сливаем локальный счётчик в общий и проверяем stop.
                with hashes_counter.get_lock():
                    hashes_counter.value += local_hashes
                local_hashes = 0
                if stop_event.is_set():
                    return
    finally:
        # Не теряем хвостовые хеши: дольём в общий счётчик.
        if local_hashes:
            with hashes_counter.get_lock():
                hashes_counter.value += local_hashes


# ─────────────────────── оркестрация пула ───────────────────────


def start_pool(
    n_workers: int,
    header_base: bytes,
    target: int,
    extranonce2: str,
) -> tuple:
    """
    Поднимает ``n_workers`` процессов, делящих [0, 2^32) поровну.

    Возвращает кортеж ``(processes, found_queue, hashes_counter, stop_event)``.
    Все объекты IPC создаются здесь, чтобы main process был их единственным
    владельцем — это упрощает корректный teardown в ``stop_pool``.
    """
    n_workers = max(1, int(n_workers))
    nonce_space = 1 << 32
    step = nonce_space // n_workers

    found_queue: mp.Queue = mp.Queue()
    hashes_counter = mp.Value("Q", 0)  # uint64, lock=True по умолчанию
    stop_event = mp.Event()

    processes: list[mp.Process] = []
    for i in range(n_workers):
        nonce_start = i * step
        # Последнему отдаём «остаток», чтобы покрыть всё пространство nonce.
        nonce_end = nonce_space if i == n_workers - 1 else (i + 1) * step
        p = mp.Process(
            target=worker,
            name=f"hope-hash-worker-{i}",
            args=(
                i, header_base, target, nonce_start, nonce_end,
                extranonce2, found_queue, hashes_counter, stop_event,
            ),
            daemon=False,
        )
        p.start()
        processes.append(p)

    logger.info(
        f"[pool] стартовал {n_workers} воркер(ов), "
        f"шаг nonce-пространства = {step:#x}"
    )
    return processes, found_queue, hashes_counter, stop_event


def stop_pool(
    processes: list,
    found_queue: "mp.Queue",
    stop_event: "mp.synchronize.Event",
    join_timeout: float = 5.0,
) -> None:
    """
    Аккуратно гасит пул: set stop_event → join → terminate (если зависли) →
    drain очереди. Очередь обязательно нужно осушить ДО join, иначе
    дочерние процессы могут заблокироваться на ``Queue.put`` под капотом
    feeder-нити (deadlock на Windows встречается особенно охотно).
    """
    stop_event.set()

    # Сначала пытаемся вытащить «уже найденные» шары — чтобы не потерялись.
    drained: list[tuple] = []
    deadline = time.time() + 0.2
    while time.time() < deadline:
        try:
            drained.append(found_queue.get_nowait())
        except Exception:
            break

    for p in processes:
        p.join(timeout=join_timeout)
    for p in processes:
        if p.is_alive():
            logger.warning(f"[pool] {p.name} не вышел за {join_timeout}с — terminate")
            p.terminate()
            p.join(timeout=2.0)

    # Закрываем queue, чтобы освободить ресурсы (важно на Windows).
    try:
        found_queue.close()
        found_queue.join_thread()
    except Exception:
        pass

    if drained:
        logger.info(f"[pool] при остановке слили {len(drained)} находок из очереди")
