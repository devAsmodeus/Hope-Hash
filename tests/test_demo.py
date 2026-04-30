"""Тест demo-режима: должен находить nonce при низкой сложности.

Многопроцессный код тестируется как чёрный ящик: запускаем `run_demo` в
spawn-процессе с маленькой diff, проверяем что вернулось True. Делаем это
в подпроцессе, чтобы pytest-нити не мешали multiprocessing на Windows.
"""

import multiprocessing as mp
import sys
import unittest


def _demo_worker(diff: float, queue: "mp.Queue") -> None:
    """Запускает run_demo в дочернем процессе и кладёт результат в очередь."""
    # Импорт внутри: spawn-дети должны импортировать заново.
    from hope_hash.demo import run_demo

    found = run_demo(n_workers=1, diff=diff)
    queue.put(found)


class TestRunDemo(unittest.TestCase):

    @unittest.skipIf(sys.platform == "win32" and sys.version_info < (3, 11),
                     "spawn overhead на старых Windows-Python слишком высок")
    def test_finds_nonce_at_low_difficulty(self):
        # diff=0.0001 → target очень высок, почти любой хеш проходит.
        # Даже с overhead spawn'а это укладывается в секунды.
        ctx = mp.get_context("spawn")
        q: "mp.Queue" = ctx.Queue()
        p = ctx.Process(target=_demo_worker, args=(0.0001, q))
        p.start()
        p.join(timeout=60)  # большой запас — запас CI runner'у на старте Python
        self.assertFalse(p.is_alive(), "demo не завершился за 60 секунд")
        self.assertEqual(p.exitcode, 0, "demo упал с ошибкой")
        self.assertTrue(q.get_nowait(), "demo вернул False — nonce не найден")


if __name__ == "__main__":
    unittest.main()
