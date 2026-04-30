"""Юнит-тесты для storage.ShareStore — журнал шаров."""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from hope_hash.storage import ShareStore


class TestShareStore(unittest.TestCase):
    def setUp(self):
        # Каждый тест в своей временной директории — БД не утечёт в репо.
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.store = ShareStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.tmpdir.cleanup()

    def _share(self, accepted=True, is_block=False, ts=None, job_id="job1"):
        """Хелпер: вставляет один шар с дефолтными значениями."""
        return self.store.record_share(
            job_id=job_id,
            nonce_hex="deadbeef",
            hash_hex="00" * 32,
            difficulty=1.0,
            accepted=accepted,
            is_block=is_block,
            ts=ts,
        )

    def test_record_share_returns_id(self):
        rid1 = self._share()
        rid2 = self._share()
        self.assertIsInstance(rid1, int)
        self.assertEqual(rid2, rid1 + 1)

    def test_total_shares_initially_zero(self):
        self.assertEqual(self.store.total_shares(), 0)
        self.assertEqual(self.store.total_shares(accepted_only=False), 0)

    def test_total_shares_counts_accepted(self):
        # 2 accepted + 1 rejected → total_shares(accepted_only=True) == 2
        self._share(accepted=True)
        self._share(accepted=True)
        self._share(accepted=False)
        self.assertEqual(self.store.total_shares(accepted_only=True), 2)
        self.assertEqual(self.store.total_shares(accepted_only=False), 3)

    def test_shares_per_hour_window(self):
        now = time.time()
        # Свежие — попадают в окно 24ч.
        self._share(ts=now - 60)
        self._share(ts=now - 600)
        # Старый — за пределами окна 24ч (двое суток назад).
        self._share(ts=now - 2 * 24 * 3600)
        # 2 шара / 24 часа ≈ 0.0833.
        rate = self.store.shares_per_hour(hours=24)
        self.assertAlmostEqual(rate, 2.0 / 24.0, places=5)

    def test_shares_per_hour_zero_when_empty(self):
        # Пустая БД и нулевые/отрицательные часы — должны давать 0.0 без ошибок.
        self.assertEqual(self.store.shares_per_hour(hours=24), 0.0)
        self.assertEqual(self.store.shares_per_hour(hours=0), 0.0)

    def test_session_lifecycle(self):
        sid = self.store.start_session("pool.example:3333", "btc1qaddr", "worker1")
        self.assertIsInstance(sid, int)
        # Читаем напрямую через само store-соединение, чтобы не плодить хендлы,
        # которые на Windows блокируют удаление файла в tearDown.
        cur = self.store._conn.execute(
            "SELECT started_at, ended_at FROM sessions WHERE id=?", (sid,)
        )
        started, ended = cur.fetchone()
        self.assertIsNotNone(started)
        self.assertIsNone(ended)
        self.store.end_session(sid)
        cur = self.store._conn.execute(
            "SELECT ended_at FROM sessions WHERE id=?", (sid,)
        )
        (ended,) = cur.fetchone()
        self.assertIsNotNone(ended)

    def test_close_is_idempotent(self):
        self.store.close()
        self.store.close()  # не должно падать

    def test_is_block_flag_persists(self):
        # Найденный блок — отдельный признак, отличный от accepted.
        rid = self._share(is_block=True)
        cur = self.store._conn.execute(
            "SELECT is_block FROM shares WHERE id=?", (rid,)
        )
        (is_block,) = cur.fetchone()
        self.assertEqual(is_block, 1)

    def test_thread_safety(self):
        # 4 нити пишут по 50 шаров каждая. После — total_shares == 200.
        n_threads = 4
        per_thread = 50

        def worker(idx: int) -> None:
            for k in range(per_thread):
                self._share(job_id=f"job-{idx}-{k}")

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.store.total_shares(), n_threads * per_thread)


if __name__ == "__main__":
    unittest.main()
