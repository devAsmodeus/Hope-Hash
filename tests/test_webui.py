"""Тесты web-дашборда: HTML, /api/stats, /api/events, /healthz, lifecycle."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import unittest
from typing import Any

from hope_hash.tui import StatsProvider
from hope_hash.webui import WebUIServer, render_html


def _free_port() -> int:
    """Биндим эфемерный порт, освобождаем — отдаём номер.

    Между release и bind есть гонка, но для unittest на одной машине это
    приемлемо. Если CI станет флаки — добавим retry-обёртку.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_get(host: str, port: int, path: str, timeout: float = 3.0):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    headers = dict(resp.getheaders())
    status = resp.status
    conn.close()
    return status, headers, body


class TestStatsProviderEvents(unittest.TestCase):
    """publish/subscribe — отдельно от HTTP, без сети."""

    def test_subscribe_and_publish(self):
        provider = StatsProvider()
        captured: list[tuple[str, dict[str, Any]]] = []
        unsub = provider.subscribe(lambda t, p: captured.append((t, p)))
        try:
            provider.record_share(accepted=None)  # share_found
            provider.record_share(accepted=True)   # share_accepted
            provider.record_share(accepted=False)  # share_rejected
            provider.update_job("abc123", 4.0)     # job
            provider.update_pool("pool.example:3333")  # pool
        finally:
            unsub()
        types = [t for t, _ in captured]
        self.assertIn("share_found", types)
        self.assertIn("share_accepted", types)
        self.assertIn("share_rejected", types)
        self.assertIn("job", types)
        self.assertIn("pool", types)

    def test_unsubscribe_idempotent(self):
        provider = StatsProvider()
        unsub = provider.subscribe(lambda t, p: None)
        unsub()
        unsub()  # второй вызов не должен падать

    def test_subscriber_exception_does_not_break_publish(self):
        provider = StatsProvider()
        ok_calls: list[str] = []

        def bad(t: str, p: dict) -> None:
            raise RuntimeError("boom")

        def good(t: str, p: dict) -> None:
            ok_calls.append(t)

        provider.subscribe(bad)
        provider.subscribe(good)
        provider.publish_event("x", {"k": 1})
        self.assertEqual(ok_calls, ["x"])

    def test_job_not_published_when_unchanged(self):
        provider = StatsProvider()
        events: list[str] = []
        provider.subscribe(lambda t, p: events.append(t))
        provider.update_job("same", 1.0)
        provider.update_job("same", 1.0)
        provider.update_job("same", 1.0)
        # Только один job-event на реальную смену.
        self.assertEqual(events.count("job"), 1)

    def test_sha_backend_default_and_setter(self):
        provider = StatsProvider()
        self.assertEqual(provider.sha_backend, "hashlib")
        provider.set_sha_backend("ctypes")
        self.assertEqual(provider.sha_backend, "ctypes")


class TestRenderHtml(unittest.TestCase):
    def test_html_contains_expected_strings(self):
        html_text = render_html()
        self.assertIn("hope-hash", html_text)
        self.assertIn("hashrate", html_text)
        self.assertIn("/api/stats", html_text)
        self.assertIn("/api/events", html_text)
        # Ни одной CDN-ссылки и ни одного <script src=>
        self.assertNotIn("<script src=", html_text)
        self.assertNotIn("cdn.", html_text)
        self.assertNotIn("googleapis", html_text)


class TestWebUIServerHTTP(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = StatsProvider(pool_url="test:1234", sha_backend="hashlib")
        self.port = _free_port()
        self.server = WebUIServer(self.provider, host="127.0.0.1", port=self.port)
        self.server.start()
        # Маленькая пауза, чтобы listening-сокет точно открылся.
        time.sleep(0.05)

    def tearDown(self) -> None:
        self.server.stop(timeout=2.0)

    def test_root_returns_html(self):
        status, headers, body = _http_get("127.0.0.1", self.port, "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        text = body.decode("utf-8")
        self.assertIn("hope-hash", text)
        self.assertIn("hashrate", text)

    def test_index_alias(self):
        status, _, _ = _http_get("127.0.0.1", self.port, "/index.html")
        self.assertEqual(status, 200)

    def test_api_stats_returns_expected_keys(self):
        # Подкормим несколько обновлений, чтобы числа были не нулевые.
        self.provider.update_hashrate(123.4, 200.0, 4)
        self.provider.update_job("job-xyz", 2.5)
        self.provider.record_share(accepted=None)
        self.provider.record_share(accepted=True)

        status, headers, body = _http_get("127.0.0.1", self.port, "/api/stats")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        data = json.loads(body)
        # Контракт API: эти ключи обязаны присутствовать.
        for key in (
            "hashrate_ema", "hashrate_human", "workers", "pool_url",
            "current_pool", "pool_difficulty", "current_job_id",
            "shares_total", "shares_accepted", "shares_rejected",
            "last_share_ts", "uptime_s", "uptime_human", "sha_backend",
            "started_at", "now",
        ):
            self.assertIn(key, data, f"missing key {key} in /api/stats payload")
        self.assertEqual(data["pool_url"], "test:1234")
        self.assertEqual(data["current_pool"], "test:1234")
        self.assertEqual(data["sha_backend"], "hashlib")
        self.assertEqual(data["current_job_id"], "job-xyz")
        self.assertEqual(data["workers"], 4)
        self.assertEqual(data["shares_total"], 1)
        self.assertEqual(data["shares_accepted"], 1)

    def test_404_on_unknown_path(self):
        status, _, _ = _http_get("127.0.0.1", self.port, "/nope")
        self.assertEqual(status, 404)

    def test_healthz_default_down(self):
        status, _, body = _http_get("127.0.0.1", self.port, "/healthz")
        # Без зарегистрированного провайдера — down/503.
        self.assertEqual(status, 503)
        data = json.loads(body)
        self.assertEqual(data["status"], "down")

    def test_healthz_uses_registered_provider(self):
        self.server.set_health_provider(
            lambda: {"status": "ok", "uptime_s": 1.0}
        )
        status, _, body = _http_get("127.0.0.1", self.port, "/healthz")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")

    def test_healthz_provider_exception_returns_503(self):
        def bad():
            raise RuntimeError("kaboom")
        self.server.set_health_provider(bad)
        status, _, body = _http_get("127.0.0.1", self.port, "/healthz")
        self.assertEqual(status, 503)
        data = json.loads(body)
        self.assertEqual(data["status"], "down")
        self.assertIn("kaboom", data["reason"])

    def test_sse_receives_published_event(self):
        # Открываем HTTP-сокет руками, потому что http.client пытается
        # прочитать всё тело сразу — для бесконечного SSE это не годится.
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=3.0)
        try:
            sock.sendall(b"GET /api/events HTTP/1.1\r\nHost: localhost\r\n\r\n")
            sock.settimeout(2.0)

            # Прочитаем заголовки до пустой строки.
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    self.fail("server closed connection before sending headers")
                buf += chunk
            header_blob, _, rest = buf.partition(b"\r\n\r\n")
            self.assertIn(b"text/event-stream", header_blob)

            # Дать handler'у успеть подписаться. subscribe() отрабатывает
            # внутри _serve_events ДО первой попытки чтения из очереди.
            time.sleep(0.2)

            # Публикуем событие — handler должен записать его в сокет.
            self.provider.publish_event("share_accepted", {"id": 42})

            # Читаем до встречи нашего event-имени.
            deadline = time.time() + 3.0
            data = rest
            while time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    chunk = b""
                if chunk:
                    data += chunk
                if b"share_accepted" in data:
                    break
            self.assertIn(b"event: share_accepted", data)
            self.assertIn(b"\"id\": 42", data)
        finally:
            sock.close()

    def test_start_is_idempotent(self):
        # Повторный start() не должен падать на bind.
        self.server.start()
        # И stats остаётся доступным.
        status, _, _ = _http_get("127.0.0.1", self.port, "/api/stats")
        self.assertEqual(status, 200)

    def test_stop_is_idempotent(self):
        self.server.stop()
        self.server.stop()  # второй вызов — no-op


class TestWebUILifecycle(unittest.TestCase):
    """Старт/стоп без накладок и без leaked-нитей."""

    def test_clean_start_stop_cycle(self):
        provider = StatsProvider()
        port = _free_port()
        server = WebUIServer(provider, host="127.0.0.1", port=port)
        server.start()
        # Пробуем достучаться.
        time.sleep(0.05)
        status, _, _ = _http_get("127.0.0.1", port, "/api/stats")
        self.assertEqual(status, 200)
        server.stop()
        # После stop() порт должен быть свободен (можем забиндить заново).
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError as e:  # pragma: no cover — диагностика
                self.fail(f"port still bound after stop(): {e}")


if __name__ == "__main__":
    unittest.main()
