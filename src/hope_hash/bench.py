"""Бенчмарк-режим: измеряет pure-Python SHA-256 хешрейт без сети.

Используется как baseline перед оптимизациями (Rust/SIMD/GPU из ROADMAP
уровня 2-3): без числа «до» сравнивать с числом «после» нечестно.

Принцип: те же multiprocessing-воркеры, что и реальный майнинг (parallel.py),
крутят SHA256d на синтетическом 76-байтовом header_base. Target ставим в 0,
чтобы ни один хеш не прошёл — воркеры просто хешируют, не отвлекаясь на
submit-логику. Через `duration_s` секунд останавливаем и считаем H/s.

Запуск:
    hope-hash --benchmark [--bench-duration 10] [--workers N]
"""

from __future__ import annotations

import multiprocessing
import platform
import struct
import sys
import time
from dataclasses import dataclass

from ._logging import logger
from .parallel import start_pool, stop_pool


@dataclass
class BenchResult:
    """Числа одного бенчмарк-прогона. Удобно для тестов и для JSON-экспорта."""
    duration_s: float
    n_workers: int
    total_hashes: int
    hashrate_hps: float

    @property
    def per_worker_hps(self) -> float:
        return self.hashrate_hps / self.n_workers if self.n_workers > 0 else 0.0


def _make_header_base() -> bytes:
    """Синтетический 76-байтовый префикс header'а (всё кроме nonce).

    Структура совпадает с реальным майнингом, чтобы mid-state-оптимизация
    в worker() работала идентично — иначе бенчмарк измерял бы не тот код.
    """
    ntime_le = struct.pack("<I", int(time.time()))
    return (
        b"\x01\x00\x00\x00" +    # version
        b"\x00" * 32 +            # prevhash
        b"\x00" * 32 +            # merkle_root
        ntime_le +                # ntime
        b"\xff\xff\x00\x1d"       # nbits
    )


def _format_rate(rate: float) -> str:
    """H/s → KH/s → MH/s. Дублирует _format_rate из miner.py намеренно:
    bench.py не должен зависеть от внутренностей mine()."""
    if rate < 1000:
        return f"{rate:.0f} H/s"
    if rate < 1_000_000:
        return f"{rate / 1000:.2f} KH/s"
    return f"{rate / 1_000_000:.2f} MH/s"


def run_benchmark(
    duration_s: float = 10.0,
    n_workers: int = 1,
    sha_backend: str = "hashlib",
    print_header: bool = True,
) -> BenchResult:
    """Один прогон бенчмарка. Не делает сетевых вызовов и не находит шары.

    Параметры:
        duration_s   — сколько секунд хешировать. Меньше 1 нет смысла —
                       накладные расходы на spawn доминируют.
        n_workers    — число процессов-воркеров (как в реальном майнинге).
        sha_backend  — ``"hashlib"`` (mid-state) или ``"ctypes"``
                       (libcrypto через EVP, без mid-state).
        print_header — печатать ли preamble (platform/python/cpu).
                       В режиме ``--backends`` мы вызываем run_benchmark
                       несколько раз и печатаем header только один раз.
    """
    n_workers = max(1, int(n_workers))
    duration_s = max(0.1, float(duration_s))
    header_base = _make_header_base()
    target = 0  # никакой хеш не пройдёт → воркеры хешируют без выходов
    extranonce2 = "00000000"

    if print_header:
        logger.info(f"[bench] platform: {platform.platform()}")
        logger.info(
            f"[bench] python:   {platform.python_version()} "
            f"({sys.implementation.name})"
        )
        logger.info(
            f"[bench] cpu:      {multiprocessing.cpu_count()} logical cores"
            f"{f' ({platform.processor()})' if platform.processor() else ''}"
        )
    logger.info(f"[bench] workers:  {n_workers}, duration: {duration_s:.1f}s, backend: {sha_backend}")
    logger.info(f"[bench] running...")

    processes, found_queue, hashes_counter, mp_stop = start_pool(
        n_workers, header_base, target, extranonce2, sha_backend=sha_backend,
    )

    start = time.perf_counter()
    # Промежуточные сэмплы — ~5 точек за прогон, чтобы пользователь видел,
    # что что-то происходит, но без лишнего шума в логе.
    report_interval = max(1.0, duration_s / 5)
    next_report = start + report_interval
    try:
        while time.perf_counter() - start < duration_s:
            time.sleep(0.05)
            now = time.perf_counter()
            if now >= next_report:
                with hashes_counter.get_lock():
                    cur = hashes_counter.value
                elapsed = now - start
                rate = cur / elapsed if elapsed > 0 else 0.0
                logger.info(
                    f"[bench]   t={elapsed:5.1f}s  "
                    f"hashes={cur:>14,}  rate={_format_rate(rate)}"
                )
                next_report = now + report_interval
    finally:
        # Замеряем elapsed ДО stop_pool, чтобы не учесть время остановки.
        # Воркеры коммитят последние батчи в hashes_counter в finally-блоке,
        # поэтому финальное значение читаем уже после stop_pool().
        elapsed = time.perf_counter() - start
        stop_pool(processes, found_queue, mp_stop)

    with hashes_counter.get_lock():
        total = hashes_counter.value
    hashrate = total / elapsed if elapsed > 0 else 0.0

    logger.info(f"[bench]")
    logger.info(f"[bench] === result ===")
    logger.info(f"[bench]   total hashes:  {total:>14,}")
    logger.info(f"[bench]   wall time:     {elapsed:>14.2f}s")
    logger.info(f"[bench]   hashrate:      {_format_rate(hashrate):>14}")
    logger.info(
        f"[bench]   per-worker:    "
        f"{_format_rate(hashrate / n_workers):>14} (workers: {n_workers})"
    )

    return BenchResult(
        duration_s=elapsed,
        n_workers=n_workers,
        total_hashes=total,
        hashrate_hps=hashrate,
    )


def available_backends() -> list[str]:
    """Список backend'ов, запускаемых ``--benchmark --backends``.

    ``hashlib`` всегда доступен (stdlib). ``ctypes`` — только если
    libcrypto загрузился. Порядок: сначала hashlib (baseline), потом
    ctypes (для сравнения «во сколько раз быстрее»).
    """
    backends = ["hashlib"]
    from . import sha_native
    if sha_native.is_available():
        backends.append("ctypes")
    return backends


def run_benchmark_all_backends(
    duration_s: float = 10.0,
    n_workers: int = 1,
) -> dict[str, BenchResult]:
    """Прогоняет бенчмарк по всем доступным backend'ам и печатает сравнение.

    Возвращает dict ``backend_name -> BenchResult``. Печатает финальную
    строку вида ``[bench] result: ctypes 1.42 MH/s (1.85x vs hashlib)``,
    которую парсят docs/CI.
    """
    backends = available_backends()
    results: dict[str, BenchResult] = {}
    for i, backend in enumerate(backends):
        # Header (platform/python/cpu) печатаем только в первом прогоне.
        results[backend] = run_benchmark(
            duration_s=duration_s, n_workers=n_workers,
            sha_backend=backend, print_header=(i == 0),
        )
        logger.info(f"[bench]")

    if not results:
        return results

    # Сводка: baseline = hashlib (всегда есть). Если есть только hashlib —
    # просто его число. Если есть ctypes — ratio относительно hashlib.
    baseline_name = "hashlib"
    baseline = results.get(baseline_name)
    logger.info(f"[bench] === backend comparison ===")
    for name, res in results.items():
        if baseline is not None and baseline.hashrate_hps > 0 and name != baseline_name:
            ratio = res.hashrate_hps / baseline.hashrate_hps
            logger.info(
                f"[bench]   {name:<10} {_format_rate(res.hashrate_hps):>14}  "
                f"({ratio:.2f}x vs {baseline_name})"
            )
        else:
            logger.info(
                f"[bench]   {name:<10} {_format_rate(res.hashrate_hps):>14}"
            )

    # Last line, легко парсится.
    if "ctypes" in results and baseline is not None and baseline.hashrate_hps > 0:
        ratio = results["ctypes"].hashrate_hps / baseline.hashrate_hps
        logger.info(
            f"[bench] result: ctypes {_format_rate(results['ctypes'].hashrate_hps)} "
            f"({ratio:.2f}x vs hashlib-midstate)"
        )
    else:
        logger.info(
            f"[bench] result: hashlib-midstate {_format_rate(baseline.hashrate_hps)} "
            f"(ctypes недоступен)"
        )

    return results
