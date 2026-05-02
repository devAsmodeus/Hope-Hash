"""Тесты для StatsProvider — без реального curses.

curses-цикл интегрировать в unittest на CI бессмысленно: на Linux
работает только в TTY, на Windows вообще отсутствует. Поэтому тестируем
только pure-Python кусок: thread-safe StatsProvider + хелперы форматирования.
"""

import threading
import time
import unittest

from hope_hash.tui import (
    StatsProvider,
    StatsSnapshot,
    format_rate,
    format_uptime,
    is_curses_available,
)


class TestStatsProvider(unittest.TestCase):
    def test_default_snapshot_is_zeros(self) -> None:
        p = StatsProvider(pool_url="example.com:1234")
        snap = p.snapshot()
        self.assertIsInstance(snap, StatsSnapshot)
        self.assertEqual(snap.pool_url, "example.com:1234")
        self.assertEqual(snap.shares_total, 0)
        self.assertEqual(snap.shares_accepted, 0)
        self.assertEqual(snap.shares_rejected, 0)
        self.assertEqual(snap.hashrate_ema, 0.0)
        self.assertIsNone(snap.current_job_id)
        self.assertIsNone(snap.last_share_ts)

    def test_update_hashrate(self) -> None:
        p = StatsProvider()
        p.update_hashrate(ema=12345.6, last_sample=11000.0, workers=8)
        snap = p.snapshot()
        self.assertAlmostEqual(snap.hashrate_ema, 12345.6)
        self.assertAlmostEqual(snap.hashrate_last, 11000.0)
        self.assertEqual(snap.workers, 8)

    def test_update_job(self) -> None:
        p = StatsProvider()
        p.update_job(job_id="abc123", pool_difficulty=2.5)
        snap = p.snapshot()
        self.assertEqual(snap.current_job_id, "abc123")
        self.assertAlmostEqual(snap.pool_difficulty, 2.5)

    def test_record_share_submitted(self) -> None:
        p = StatsProvider()
        before = time.time()
        p.record_share(accepted=None)
        snap = p.snapshot()
        self.assertEqual(snap.shares_total, 1)
        self.assertEqual(snap.shares_accepted, 0)
        self.assertEqual(snap.shares_rejected, 0)
        self.assertIsNotNone(snap.last_share_ts)
        self.assertGreaterEqual(snap.last_share_ts, before)

    def test_record_share_accepted_does_not_double_count(self) -> None:
        # accepted=True должен увеличить только accepted, не total —
        # total отражает «отправлено», accepted/rejected — «подтверждено».
        p = StatsProvider()
        p.record_share(accepted=None)  # отправили
        p.record_share(accepted=True)  # пул подтвердил
        snap = p.snapshot()
        self.assertEqual(snap.shares_total, 1)
        self.assertEqual(snap.shares_accepted, 1)
        self.assertEqual(snap.shares_rejected, 0)

    def test_record_share_rejected(self) -> None:
        p = StatsProvider()
        p.record_share(accepted=None)
        p.record_share(accepted=False)
        snap = p.snapshot()
        self.assertEqual(snap.shares_total, 1)
        self.assertEqual(snap.shares_accepted, 0)
        self.assertEqual(snap.shares_rejected, 1)

    def test_snapshot_is_a_copy(self) -> None:
        # Мутация снапшота не должна влиять на провайдера —
        # иначе race условия между TUI и mine() сломают учёт.
        p = StatsProvider()
        p.update_hashrate(100.0, 90.0, 4)
        snap1 = p.snapshot()
        snap1.hashrate_ema = 999999.0
        snap2 = p.snapshot()
        self.assertAlmostEqual(snap2.hashrate_ema, 100.0)

    def test_thread_safety_smoke(self) -> None:
        # Несколько писателей и читателей — никаких эксепшнов.
        p = StatsProvider()
        stop = threading.Event()

        def writer() -> None:
            for i in range(200):
                p.update_hashrate(float(i), float(i - 1), 4)
                p.record_share(accepted=None)
                if stop.is_set():
                    return

        def reader() -> None:
            for _ in range(500):
                _ = p.snapshot()
                if stop.is_set():
                    return

        ts = [threading.Thread(target=writer) for _ in range(3)]
        ts += [threading.Thread(target=reader) for _ in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=10)
        stop.set()

        snap = p.snapshot()
        # 3 writers × 200 итераций = 600 шар.
        self.assertEqual(snap.shares_total, 600)


class TestFormatHelpers(unittest.TestCase):
    def test_format_rate_hps(self) -> None:
        self.assertEqual(format_rate(0), "0 H/s")
        self.assertEqual(format_rate(999), "999 H/s")

    def test_format_rate_khps(self) -> None:
        self.assertIn("KH/s", format_rate(1500))
        self.assertIn("1.50", format_rate(1500))

    def test_format_rate_mhps(self) -> None:
        self.assertIn("MH/s", format_rate(2_500_000))
        self.assertIn("2.50", format_rate(2_500_000))

    def test_format_uptime_short(self) -> None:
        self.assertEqual(format_uptime(0), "00:00:00")
        self.assertEqual(format_uptime(65), "00:01:05")
        self.assertEqual(format_uptime(3600), "01:00:00")

    def test_format_uptime_with_days(self) -> None:
        # 1 день и 2 часа.
        self.assertIn("1d", format_uptime(86400 + 7200))


class TestCursesAvailability(unittest.TestCase):
    def test_is_curses_available_returns_bool(self) -> None:
        # Не утверждаем True/False — на Windows без windows-curses будет False,
        # на Linux/macOS будет True. Проверяем только что функция работает.
        self.assertIsInstance(is_curses_available(), bool)


if __name__ == "__main__":
    unittest.main()
