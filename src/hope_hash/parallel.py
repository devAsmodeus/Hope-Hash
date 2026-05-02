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

import hashlib
import multiprocessing as mp
import queue
import struct

from ._logging import logger


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
    sha_backend: str = "hashlib",
) -> None:
    """
    Перебирает nonce в диапазоне [nonce_start, nonce_end), считает SHA256d.

    Если хеш ≤ target — кладёт ``(nonce_hex_be, hash_hex_be, extranonce2)``
    в ``found_queue``. Каждые ``HASHES_PER_TICK`` хешей:
      - проверяет ``stop_event`` (рано выйти при смене job / Ctrl+C);
      - инкрементирует ``hashes_counter`` (для расчёта EMA-хешрейта в main).

    Сам ``submit`` делается из main process — здесь только находка.
    Сигнатура полностью pickle-able: bytes/int/str + примитивы mp.

    ``sha_backend`` управляет, чем хешировать:
      - ``"hashlib"`` (default) — mid-state оптимизация через ``hashlib.copy()``.
        Это hot path, проверенный на реальных блоках.
      - ``"ctypes"`` — каждая итерация = ``sha256d(header_base+nonce_le)``
        через libcrypto. Без mid-state. Используется для бенчмарка.
        Если libcrypto не загрузился, прозрачно падаем на hashlib.
    """
    if sha_backend == "ctypes":
        from . import sha_native
        if sha_native.is_available():
            _worker_ctypes(
                header_base, target, nonce_start, nonce_end, extranonce2,
                found_queue, hashes_counter, stop_event, sha_native.sha256d,
            )
            return
        # libcrypto не нашёлся в воркере (на нестандартной машине) —
        # тихо переключаемся на hashlib, чтобы майнер не падал.
        # Логируем в воркер-процессе один раз через логгер пакета.
        logger.warning("[sha] worker: ctypes недоступен, fallback hashlib")

    _worker_hashlib_midstate(
        header_base, target, nonce_start, nonce_end, extranonce2,
        found_queue, hashes_counter, stop_event,
    )


def _worker_hashlib_midstate(
    header_base: bytes,
    target: int,
    nonce_start: int,
    nonce_end: int,
    extranonce2: str,
    found_queue: "mp.Queue",
    hashes_counter: "mp.sharedctypes.Synchronized",
    stop_event: "mp.synchronize.Event",
) -> None:
    """Hot path: mid-state SHA-256 через ``hashlib.copy()``.

    Block header = 80 байт = 64 + 16. SHA-256 обрабатывает данные блоками
    по 64 байта, поэтому первые 64 байта header_base — константа в пределах
    одного nonce-цикла. Вычисляем SHA-256 mid-state один раз, а в горячем
    цикле делаем только copy() + дохэшируем оставшиеся 16 байт.
    Экономия: ~половина первого SHA-256-прохода на каждый nonce.
    """
    inner_mid = hashlib.sha256()
    inner_mid.update(header_base[:64])
    tail_prefix = header_base[64:]   # 12 байт: merkle_root[28:] + ntime + nbits

    local_hashes = 0  # копим локально, чтобы реже дёргать Lock на Value
    nonce = nonce_start
    try:
        while nonce < nonce_end:
            # Горячий путь: copy mid-state + дохэшируем tail_prefix + nonce.
            inner_h = inner_mid.copy()
            inner_h.update(tail_prefix)
            inner_h.update(struct.pack("<I", nonce))
            # Внешний SHA-256 поверх дайджеста (double-SHA256).
            h = hashlib.sha256(inner_h.digest()).digest()

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


def _worker_ctypes(
    header_base: bytes,
    target: int,
    nonce_start: int,
    nonce_end: int,
    extranonce2: str,
    found_queue: "mp.Queue",
    hashes_counter: "mp.sharedctypes.Synchronized",
    stop_event: "mp.synchronize.Event",
    sha256d,
) -> None:
    """ctypes-backend: ``sha256d(header_base + nonce_le)`` каждую итерацию.

    Без mid-state — это сознательная плата за честный замер скорости
    нативного backend. Если хочется полной производительности — оставляйте
    backend hashlib (mid-state даёт ~2x).
    """
    local_hashes = 0
    nonce = nonce_start
    try:
        while nonce < nonce_end:
            buf = header_base + struct.pack("<I", nonce)
            h = sha256d(buf)
            h_int = int.from_bytes(h[::-1], "big")
            if h_int <= target:
                nonce_hex = struct.pack(">I", nonce).hex()
                hash_hex = h[::-1].hex()
                found_queue.put((nonce_hex, hash_hex, extranonce2))

            nonce += 1
            local_hashes += 1

            if local_hashes >= HASHES_PER_TICK:
                with hashes_counter.get_lock():
                    hashes_counter.value += local_hashes
                local_hashes = 0
                if stop_event.is_set():
                    return
    finally:
        if local_hashes:
            with hashes_counter.get_lock():
                hashes_counter.value += local_hashes


# ─────────────────────── оркестрация пула ───────────────────────


def start_pool(
    n_workers: int,
    header_base: bytes,
    target: int,
    extranonce2: str,
    sha_backend: str = "hashlib",
) -> tuple:
    """
    Поднимает ``n_workers`` процессов, делящих [0, 2^32) поровну.

    Возвращает кортеж ``(processes, found_queue, hashes_counter, stop_event)``.
    Все объекты IPC создаются здесь, чтобы main process был их единственным
    владельцем — это упрощает корректный teardown в ``stop_pool``.

    ``sha_backend`` пробрасывается в воркер: ``"hashlib"`` (mid-state)
    или ``"ctypes"`` (libcrypto через EVP). См. ``worker()``.
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
                sha_backend,
            ),
            daemon=False,
        )
        p.start()
        processes.append(p)

    logger.info(
        f"[pool] стартовал {n_workers} воркер(ов), "
        f"шаг nonce-пространства = {step:#x}, sha_backend={sha_backend}"
    )
    return processes, found_queue, hashes_counter, stop_event


def stop_pool(
    processes: list,
    found_queue: "mp.Queue",
    stop_event: "mp.synchronize.Event",
    join_timeout: float = 5.0,
) -> None:
    """
    Аккуратно гасит пул: set stop_event → drain очереди → join → terminate
    (если зависли). Очередь обязательно нужно осушить ДО join, иначе дочерние
    процессы могут заблокироваться на ``Queue.put`` под капотом feeder-нити
    (deadlock на Windows встречается особенно охотно).
    """
    stop_event.set()

    # Сначала вытаскиваем «уже найденные» шары — чтобы не потерялись.
    # Раньше тут было время-ориентированное окно (0.2с), но это магическое число
    # без обоснования: воркер ставит put в очередь до проверки stop_event, поэтому
    # к моменту вызова stop_pool() все находки уже либо в очереди, либо в feeder-
    # буфере воркера (который доставит их в Queue до выхода). Достаточно опустошить
    # очередь до queue.Empty с safety-кэпом против бесконечного цикла, если по
    # какой-то причине поток ещё пишет.
    drained: list[tuple] = []
    drain_cap = 1024  # на пул в 16 воркеров с очень низким diff — с большим запасом
    while len(drained) < drain_cap:
        try:
            drained.append(found_queue.get_nowait())
        except queue.Empty:
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
