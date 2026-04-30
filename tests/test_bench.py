"""Тесты для bench.py: проверяют форму результата и факт хеширования.

Запускаем короткий бенчмарк (1с, 1 воркер) в spawn-подпроцессе — те же
причины, что в test_demo.py: multiprocessing на Windows плохо живёт
с pytest-нитями в том же процессе.
"""

import multiprocessing as mp
import sys
import unittest


def _bench_worker(duration_s: float, n_workers: int, queue: "mp.Queue") -> None:
    from hope_hash.bench import run_benchmark

    result = run_benchmark(duration_s=duration_s, n_workers=n_workers)
    queue.put({
        "duration_s": result.duration_s,
        "n_workers": result.n_workers,
        "total_hashes": result.total_hashes,
        "hashrate_hps": result.hashrate_hps,
        "per_worker_hps": result.per_worker_hps,
    })


class TestRunBenchmark(unittest.TestCase):

    def test_short_run_produces_positive_hashrate(self):
        ctx = mp.get_context("spawn")
        q: "mp.Queue" = ctx.Queue()
        # 1 секунда хватает: даже на CI runner'ах за 1с pure-Python успеет
        # сделать десятки тысяч хешей.
        p = ctx.Process(target=_bench_worker, args=(1.0, 1, q))
        p.start()
        p.join(timeout=60)
        self.assertFalse(p.is_alive(), "benchmark не завершился за 60 секунд")
        self.assertEqual(p.exitcode, 0, "benchmark упал с ошибкой")

        result = q.get_nowait()
        self.assertEqual(result["n_workers"], 1)
        self.assertGreater(result["total_hashes"], 0, "ни одного хеша не посчитано")
        self.assertGreater(result["hashrate_hps"], 0, "хешрейт нулевой")
        # per_worker_hps == hashrate_hps при n_workers=1
        self.assertAlmostEqual(
            result["per_worker_hps"], result["hashrate_hps"], delta=0.01
        )
        # Длительность должна быть близка к запрошенной (с допуском на overhead).
        self.assertGreater(result["duration_s"], 0.5)
        self.assertLess(result["duration_s"], 5.0)


class TestBenchResultDataclass(unittest.TestCase):
    """Чистые свойства BenchResult — без multiprocessing."""

    def test_per_worker_calculation(self):
        from hope_hash.bench import BenchResult

        r = BenchResult(
            duration_s=10.0, n_workers=4, total_hashes=4_000_000, hashrate_hps=400_000.0
        )
        self.assertEqual(r.per_worker_hps, 100_000.0)

    def test_per_worker_zero_workers(self):
        from hope_hash.bench import BenchResult

        # Защита от деления на ноль: 0 воркеров → 0 H/s на воркера.
        r = BenchResult(duration_s=1.0, n_workers=0, total_hashes=0, hashrate_hps=0.0)
        self.assertEqual(r.per_worker_hps, 0.0)


if __name__ == "__main__":
    unittest.main()
