"""Юнит-тесты для metrics — Prometheus экспортёр."""

import socket
import unittest
import urllib.error
import urllib.request

from hope_hash.metrics import Metrics, MetricsServer


def _free_port() -> int:
    """Захватывает свободный порт у ОС, возвращает его.

    Между close() и последующим bind() есть микро-окно гонки, но для
    локальных юнит-тестов это допустимо.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestMetricsRegistry(unittest.TestCase):
    def test_counter_increments(self) -> None:
        m = Metrics()
        m.counter_inc("foo", 1)
        m.counter_inc("foo", 2)
        body = m.render().decode()
        self.assertIn("foo 3", body)

    def test_counter_default_step_is_one(self) -> None:
        m = Metrics()
        m.counter_inc("hits")
        m.counter_inc("hits")
        m.counter_inc("hits")
        self.assertIn("hits 3", m.render().decode())

    def test_counter_rejects_negative(self) -> None:
        m = Metrics()
        with self.assertRaises(ValueError):
            m.counter_inc("bad", -1)

    def test_gauge_overwrites(self) -> None:
        m = Metrics()
        m.gauge_set("hashrate", 100.0)
        m.gauge_set("hashrate", 152334.5)
        body = m.render().decode()
        self.assertIn("hashrate 152334.5", body)
        self.assertNotIn("hashrate 100", body)

    def test_render_includes_help_and_type(self) -> None:
        m = Metrics()
        m.counter_inc("shares_total", 1, help="Total accepted shares")
        m.gauge_set("hashrate_hps", 42.0, help="Current hashrate in H/s")
        body = m.render().decode()
        self.assertIn("# HELP shares_total Total accepted shares", body)
        self.assertIn("# TYPE shares_total counter", body)
        self.assertIn("# HELP hashrate_hps Current hashrate in H/s", body)
        self.assertIn("# TYPE hashrate_hps gauge", body)

    def test_render_includes_uptime(self) -> None:
        m = Metrics()
        body = m.render().decode()
        self.assertIn("# TYPE hopehash_uptime_seconds gauge", body)
        self.assertIn("hopehash_uptime_seconds ", body)

    def test_render_returns_bytes(self) -> None:
        m = Metrics()
        m.counter_inc("foo")
        out = m.render()
        self.assertIsInstance(out, bytes)
        # должно валидно декодироваться в UTF-8
        out.decode("utf-8")

    def test_invalid_metric_name_sanitized(self) -> None:
        m = Metrics()
        m.counter_inc("foo-bar.baz", 7)
        body = m.render().decode()
        self.assertIn("foo_bar_baz 7", body)
        self.assertNotIn("foo-bar.baz", body)

    def test_thread_safety_concurrent_inc(self) -> None:
        # Параллельные инкременты не должны терять обновления.
        import threading

        m = Metrics()

        def worker() -> None:
            for _ in range(1000):
                m.counter_inc("c")

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertIn("c 5000", m.render().decode())


class TestMetricsServer(unittest.TestCase):
    def setUp(self) -> None:
        self.metrics = Metrics()
        self.metrics.counter_inc("test_counter", 5, help="test counter")
        self.server = MetricsServer(self.metrics, port=_free_port())
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()

    def test_metrics_endpoint(self) -> None:
        with urllib.request.urlopen(self.server.url, timeout=2) as resp:
            body = resp.read().decode()
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
        self.assertEqual(status, 200)
        self.assertIn("test_counter 5", body)
        self.assertIn("text/plain", ctype)
        self.assertIn("version=0.0.4", ctype)

    def test_other_path_returns_404(self) -> None:
        url = self.server.url.replace("/metrics", "/foo")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url, timeout=2)
        self.assertEqual(ctx.exception.code, 404)

    def test_root_path_returns_404(self) -> None:
        url = f"http://{self.server.host}:{self.server.port}/"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url, timeout=2)
        self.assertEqual(ctx.exception.code, 404)

    def test_stop_is_idempotent(self) -> None:
        self.server.stop()
        self.server.stop()  # повторный stop не должен падать

    def test_double_start_is_idempotent(self) -> None:
        # уже запущен в setUp; повторный start не должен падать или ронять сервер.
        self.server.start()
        with urllib.request.urlopen(self.server.url, timeout=2) as resp:
            self.assertEqual(resp.status, 200)

    def test_metrics_reflect_live_updates(self) -> None:
        # После старта сервер должен отдавать актуальное состояние регистра.
        self.metrics.gauge_set("live_gauge", 99.5)
        with urllib.request.urlopen(self.server.url, timeout=2) as resp:
            body = resp.read().decode()
        self.assertIn("live_gauge 99.5", body)

    def test_url_property(self) -> None:
        self.assertTrue(self.server.url.startswith("http://127.0.0.1:"))
        self.assertTrue(self.server.url.endswith("/metrics"))


if __name__ == "__main__":
    unittest.main()
