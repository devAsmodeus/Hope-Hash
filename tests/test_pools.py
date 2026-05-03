"""Тесты PoolList: round-robin failover, дедуп, парсинг spec."""

import unittest

from hope_hash.pools import PoolList, parse_pool_spec


class TestParsePoolSpec(unittest.TestCase):
    def test_host_port(self):
        self.assertEqual(parse_pool_spec("foo.example.com:1234"), ("foo.example.com", 1234))

    def test_host_only_uses_default_port(self):
        self.assertEqual(parse_pool_spec("foo.example.com"), ("foo.example.com", 3333))

    def test_host_only_custom_default(self):
        self.assertEqual(parse_pool_spec("foo", default_port=4444), ("foo", 4444))

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_pool_spec("")

    def test_whitespace_raises(self):
        with self.assertRaises(ValueError):
            parse_pool_spec("   ")

    def test_invalid_port_raises(self):
        with self.assertRaises(ValueError):
            parse_pool_spec("foo:notaport")

    def test_port_out_of_range(self):
        with self.assertRaises(ValueError):
            parse_pool_spec("foo:99999")
        with self.assertRaises(ValueError):
            parse_pool_spec("foo:0")

    def test_no_host_raises(self):
        with self.assertRaises(ValueError):
            parse_pool_spec(":3333")


class TestPoolListBasics(unittest.TestCase):
    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            PoolList([])

    def test_zero_threshold_raises(self):
        with self.assertRaises(ValueError):
            PoolList([("a", 1)], rotate_after_failures=0)

    def test_single_pool(self):
        p = PoolList([("a", 1)])
        self.assertEqual(p.current(), ("a", 1))
        self.assertEqual(p.size, 1)
        self.assertEqual(p.current_url(), "a:1")

    def test_dedup(self):
        # Дубль (case-insensitive) должен игнорироваться: ротация по
        # одному и тому же пулу бессмысленна.
        p = PoolList([("a", 1), ("A", 1), ("b", 2)])
        self.assertEqual(p.size, 2)
        self.assertEqual(p.all_endpoints(), [("a", 1), ("b", 2)])


class TestPoolListRotation(unittest.TestCase):
    def test_no_rotation_below_threshold(self):
        p = PoolList([("a", 1), ("b", 2)], rotate_after_failures=3)
        for _ in range(2):
            self.assertFalse(p.mark_failed())
        self.assertEqual(p.current(), ("a", 1))

    def test_rotates_at_threshold(self):
        p = PoolList([("a", 1), ("b", 2)], rotate_after_failures=2)
        self.assertFalse(p.mark_failed())  # 1
        self.assertTrue(p.mark_failed())   # 2 → ротация
        self.assertEqual(p.current(), ("b", 2))

    def test_wrap_around(self):
        p = PoolList([("a", 1), ("b", 2), ("c", 3)], rotate_after_failures=1)
        self.assertTrue(p.mark_failed())
        self.assertEqual(p.current(), ("b", 2))
        self.assertTrue(p.mark_failed())
        self.assertEqual(p.current(), ("c", 3))
        self.assertTrue(p.mark_failed())
        self.assertEqual(p.current(), ("a", 1))  # wrap

    def test_single_pool_rotates_to_self(self):
        p = PoolList([("a", 1)], rotate_after_failures=1)
        self.assertTrue(p.mark_failed())
        self.assertEqual(p.current(), ("a", 1))

    def test_mark_success_resets_failures(self):
        p = PoolList([("a", 1), ("b", 2)], rotate_after_failures=3)
        p.mark_failed()
        p.mark_failed()
        self.assertEqual(p.failures(), 2)
        p.mark_success()
        self.assertEqual(p.failures(), 0)
        # И счётчик ротаций тоже:
        self.assertFalse(p.full_cycle_failed())

    def test_full_cycle_failed_after_all_rotations(self):
        p = PoolList([("a", 1), ("b", 2)], rotate_after_failures=1)
        self.assertFalse(p.full_cycle_failed())
        p.mark_failed()  # rotate to b
        self.assertFalse(p.full_cycle_failed())
        p.mark_failed()  # rotate to a (wrap), 2 ротации = size
        self.assertTrue(p.full_cycle_failed())

    def test_full_cycle_reset_after_success(self):
        p = PoolList([("a", 1), ("b", 2)], rotate_after_failures=1)
        p.mark_failed()
        p.mark_failed()
        self.assertTrue(p.full_cycle_failed())
        p.mark_success()
        self.assertFalse(p.full_cycle_failed())

    def test_reset_round_does_not_change_index(self):
        p = PoolList([("a", 1), ("b", 2)], rotate_after_failures=1)
        p.mark_failed()
        idx_before = p.current()
        p.reset_round()
        self.assertEqual(p.current(), idx_before)
        self.assertFalse(p.full_cycle_failed())

    def test_manual_rotate(self):
        p = PoolList([("a", 1), ("b", 2)])
        new = p.rotate()
        self.assertEqual(new, ("b", 2))
        self.assertEqual(p.current(), ("b", 2))


class TestPoolListThreadSafety(unittest.TestCase):
    """Тут не гоняем гонки массово — просто проверяем, что lock не deadlock'ится."""

    def test_nested_calls_dont_deadlock(self):
        # rotate() вызывает _rotate_locked() под self._lock; mark_failed() тоже.
        # Если лок не RLock — будет deadlock на самовызове.
        p = PoolList([("a", 1), ("b", 2)], rotate_after_failures=1)
        for _ in range(10):
            p.mark_failed()
        self.assertIn(p.current(), [("a", 1), ("b", 2)])


if __name__ == "__main__":
    unittest.main()
