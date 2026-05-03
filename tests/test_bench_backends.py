"""Тесты бенчмарка с несколькими backend'ами."""

import unittest

from hope_hash import sha_native
from hope_hash.bench import available_backends, run_benchmark, run_benchmark_all_backends


class TestAvailableBackends(unittest.TestCase):
    def test_hashlib_always_present(self):
        backends = available_backends()
        self.assertIn("hashlib", backends)

    def test_hashlib_first(self):
        # Baseline должен быть первым — это convention для отображения.
        self.assertEqual(available_backends()[0], "hashlib")

    def test_ctypes_present_iff_available(self):
        backends = available_backends()
        if sha_native.is_available():
            self.assertIn("ctypes", backends)
        else:
            self.assertNotIn("ctypes", backends)


class TestRunBenchmarkBackend(unittest.TestCase):
    def test_hashlib_runs(self):
        # Очень короткий прогон, лишь бы не упало.
        result = run_benchmark(duration_s=0.5, n_workers=1, sha_backend="hashlib")
        self.assertGreater(result.total_hashes, 0)
        self.assertGreater(result.hashrate_hps, 0)

    def test_ctypes_runs_if_available(self):
        if not sha_native.is_available():
            self.skipTest("libcrypto не загружен")
        result = run_benchmark(duration_s=0.5, n_workers=1, sha_backend="ctypes")
        self.assertGreater(result.total_hashes, 0)


class TestRunBenchmarkAllBackends(unittest.TestCase):
    def test_returns_dict_with_hashlib(self):
        results = run_benchmark_all_backends(duration_s=0.5, n_workers=1)
        self.assertIn("hashlib", results)

    def test_results_have_positive_hashrate(self):
        results = run_benchmark_all_backends(duration_s=0.5, n_workers=1)
        for name, res in results.items():
            self.assertGreater(res.hashrate_hps, 0, f"{name} зирорейт")
            self.assertGreater(res.total_hashes, 0, f"{name} ноль хешей")

    def test_includes_ctypes_if_available(self):
        results = run_benchmark_all_backends(duration_s=0.5, n_workers=1)
        if sha_native.is_available():
            self.assertIn("ctypes", results)


if __name__ == "__main__":
    unittest.main()
