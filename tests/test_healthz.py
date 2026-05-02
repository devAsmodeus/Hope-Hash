"""Тесты /healthz endpoint и build_health_snapshot.

Стратегия:
- Чистая функция build_health_snapshot тестируется на детерминистичных
  параметрах (передаём now=...) — никакой реальной хронологии.
- HTTP-слой /healthz прогоняем через настоящий MetricsServer на свободном
  порту: зеркалит интеграцию с Prometheus-сервером.
"""

import json
import socket
import time
import unittest
import urllib.error
import urllib.request

from hope_hash.metrics import Metrics, MetricsServer, build_health_snapshot


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestBuildHealthSnapshot(unittest.TestCase):
    def test_ok_when_everything_fresh(self) -> None:
        now = 1000.0
        snap = build_health_snapshot(
            reader_alive=True,
            hashrate_ema=100000.0,
            hashrate_ts=now - 5,         # 5с назад — свежее окна 30с
            last_share_ts=now - 60,       # минута назад — внутри 600с
            started_at=now - 300,
            stale_after_s=600,
            now=now,
        )
        self.assertEqual(snap["status"], "ok")
        self.assertGreater(snap["uptime_s"], 0)

    def test_down_when_reader_dead(self) -> None:
        now = 1000.0
        snap = build_health_snapshot(
            reader_alive=False,
            hashrate_ema=100000.0,
            hashrate_ts=now,
            last_share_ts=now,
            started_at=now - 100,
            now=now,
        )
        self.assertEqual(snap["status"], "down")
        self.assertIn("reader", snap["reason"])

    def test_degraded_when_hashrate_stale(self) -> None:
        now = 1000.0
        snap = build_health_snapshot(
            reader_alive=True,
            hashrate_ema=100000.0,
            hashrate_ts=now - 200,        # старше 30с-окна → stale
            last_share_ts=now - 60,
            started_at=now - 1000,
            now=now,
        )
        self.assertEqual(snap["status"], "degraded")

    def test_degraded_when_no_recent_share(self) -> None:
        now = 1000.0
        snap = build_health_snapshot(
            reader_alive=True,
            hashrate_ema=100000.0,
            hashrate_ts=now - 5,
            last_share_ts=now - 1000,     # старше 600с-окна
            started_at=now - 5000,
            stale_after_s=600,
            now=now,
        )
        self.assertEqual(snap["status"], "degraded")

    def test_ok_at_startup_no_share_yet(self) -> None:
        # Только что запустились (uptime < stale_after_s), шар нет ещё —
        # это должно считаться ok, иначе healthz будет flap'ать каждый старт.
        now = 1000.0
        snap = build_health_snapshot(
            reader_alive=True,
            hashrate_ema=50000.0,
            hashrate_ts=now - 3,
            last_share_ts=None,
            started_at=now - 30,
            stale_after_s=600,
            now=now,
        )
        self.assertEqual(snap["status"], "ok")

    def test_zero_hashrate_is_degraded(self) -> None:
        now = 1000.0
        snap = build_health_snapshot(
            reader_alive=True,
            hashrate_ema=0.0,
            hashrate_ts=now,
            last_share_ts=now - 60,
            started_at=now - 1000,
            now=now,
        )
        self.assertEqual(snap["status"], "degraded")


class TestHealthzHTTP(unittest.TestCase):
    def setUp(self) -> None:
        self.metrics = Metrics()
        self.port = _free_port()
        self.server = MetricsServer(self.metrics, port=self.port)
        self.server.start()
        # Дать серверу подняться.
        time.sleep(0.05)

    def tearDown(self) -> None:
        self.server.stop()

    def _get(self, path: str) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                return e.code, json.loads(body)
            except json.JSONDecodeError:
                return e.code, {"raw": body}

    def test_healthz_503_when_no_provider(self) -> None:
        status, body = self._get("/healthz")
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "down")

    def test_healthz_200_when_ok(self) -> None:
        self.server.set_health_provider(lambda: {"status": "ok", "uptime_s": 1.0})
        status, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_healthz_200_when_degraded(self) -> None:
        self.server.set_health_provider(
            lambda: {"status": "degraded", "reason": "no recent share"}
        )
        status, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "degraded")

    def test_healthz_503_when_provider_returns_down(self) -> None:
        self.server.set_health_provider(lambda: {"status": "down", "reason": "x"})
        status, _ = self._get("/healthz")
        self.assertEqual(status, 503)

    def test_healthz_503_when_provider_raises(self) -> None:
        def bad_provider() -> dict:
            raise RuntimeError("kaboom")
        self.server.set_health_provider(bad_provider)
        status, body = self._get("/healthz")
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "down")
        self.assertIn("kaboom", body["reason"])

    def test_metrics_still_works(self) -> None:
        # /metrics не должен сломаться от добавления /healthz.
        self.metrics.counter_inc("smoke_total", 5)
        url = f"http://127.0.0.1:{self.port}/metrics"
        with urllib.request.urlopen(url, timeout=2) as resp:
            body = resp.read().decode("utf-8")
        self.assertIn("smoke_total 5", body)


if __name__ == "__main__":
    unittest.main()
