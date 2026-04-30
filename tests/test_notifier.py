"""Юнит-тесты для notifier — Telegram через urllib.

Сетевые вызовы НЕ делаем. Мокаем urllib.request.urlopen через unittest.mock.
"""

import os
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

from hope_hash.notifier import TelegramNotifier


def _make_mock_response(status: int = 200) -> MagicMock:
    """Хелпер: собирает мок-объект, поддерживающий with-протокол urlopen."""
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=None)
    return mock_resp


class TestNotifierDisabled(unittest.TestCase):
    """Поведение в disabled-режиме: всё должно быть no-op без падений."""

    def test_no_token_disabled(self):
        n = TelegramNotifier(token=None, chat_id="x")
        self.assertFalse(n.enabled)
        n.notify("hello")  # no-op
        n.shutdown()

    def test_empty_chat_id_disabled(self):
        # Пустая строка — это «не задано» по нашему контракту.
        n = TelegramNotifier(token="123:ABC", chat_id="")
        self.assertFalse(n.enabled)
        n.notify("hello")
        n.shutdown()

    def test_both_none_disabled(self):
        n = TelegramNotifier(token=None, chat_id=None)
        self.assertFalse(n.enabled)
        n.shutdown()

    def test_disabled_methods_dont_throw(self):
        # Все notify_* методы должны не падать в disabled-режиме.
        n = TelegramNotifier()
        self.assertFalse(n.enabled)
        n.notify("plain")
        n.notify_started("bc1qxyz...", "worker1")
        n.notify_stopped()
        n.notify_disconnected("timeout")
        n.notify_reconnected()
        n.notify_share_accepted("jobA", 1.5)
        n.notify_block_found("0000abcd" * 8, 999999)
        n.notify_block_found("0000abcd" * 8)  # без height
        n.shutdown()
        # Повторный shutdown идемпотентен.
        n.shutdown()

    def test_from_env_disabled_when_no_vars(self):
        # Без переменных окружения — disabled.
        with patch.dict(os.environ, {}, clear=True):
            n = TelegramNotifier.from_env()
            self.assertFalse(n.enabled)
            n.shutdown()

    def test_from_env_enabled_when_vars_set(self):
        env = {
            "HOPE_HASH_TELEGRAM_TOKEN": "111:AAA",
            "HOPE_HASH_TELEGRAM_CHAT_ID": "999",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("hope_hash.notifier.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value = _make_mock_response()
                n = TelegramNotifier.from_env()
                self.assertTrue(n.enabled)
                self.assertEqual(n.token, "111:AAA")
                self.assertEqual(n.chat_id, "999")
                n.shutdown()


class TestNotifierEnabled(unittest.TestCase):
    """Поведение в enabled-режиме: проверяем формат вызовов и текстов."""

    @patch("hope_hash.notifier.urllib.request.urlopen")
    def test_send_calls_telegram_api(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_response()

        n = TelegramNotifier(token="123:ABC", chat_id="456")
        n.notify("test message")
        n.shutdown()

        self.assertTrue(mock_urlopen.called)
        req = mock_urlopen.call_args[0][0]
        # URL содержит токен — это полный путь к sendMessage.
        self.assertIn("123:ABC", req.full_url)
        self.assertIn("api.telegram.org", req.full_url)
        # Метод POST.
        self.assertEqual(req.get_method(), "POST")
        # Body содержит chat_id и text в urlencoded виде.
        body = req.data.decode("utf-8")
        self.assertIn("chat_id=456", body)
        self.assertIn("text=test+message", body)

    @patch("hope_hash.notifier.urllib.request.urlopen")
    def test_share_accepted_message_format(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_response()

        n = TelegramNotifier(token="t", chat_id="c")
        n.notify_share_accepted(job_id="abc123", difficulty=2.5)
        n.shutdown()

        body = mock_urlopen.call_args[0][0].data.decode("utf-8")
        # urlencode заменяет пробелы на + и не-ASCII на %XX,
        # достаточно проверить ключевые подстроки.
        self.assertIn("abc123", body)
        self.assertIn("2.5", body)

    @patch("hope_hash.notifier.urllib.request.urlopen")
    def test_block_found_message_format(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_response()

        n = TelegramNotifier(token="t", chat_id="c")
        block_hash = "00000000000000000007abc123def456" + "ff" * 16
        n.notify_block_found(hash_hex=block_hash, height=800000)
        n.shutdown()

        body = mock_urlopen.call_args[0][0].data.decode("utf-8")
        # Высота попадает в текст.
        self.assertIn("800000", body)
        # Префикс хеша попадает (первые 32 hex-символа).
        self.assertIn(block_hash[:32], body)

    @patch("hope_hash.notifier.urllib.request.urlopen")
    def test_block_found_without_height(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_response()

        n = TelegramNotifier(token="t", chat_id="c")
        n.notify_block_found(hash_hex="deadbeef" * 8)
        n.shutdown()

        self.assertTrue(mock_urlopen.called)

    @patch("hope_hash.notifier.urllib.request.urlopen")
    def test_shutdown_drains_queue(self, mock_urlopen):
        # Кладём 5 сообщений → shutdown → проверяем что все 5 ушли.
        mock_urlopen.return_value = _make_mock_response()

        n = TelegramNotifier(token="t", chat_id="c")
        for i in range(5):
            n.notify(f"msg-{i}")
        n.shutdown()

        # Каждый notify → один urlopen. Sentinel(None) уходит без HTTP-вызова.
        self.assertEqual(mock_urlopen.call_count, 5)

    @patch("hope_hash.notifier.urllib.request.urlopen")
    def test_send_failure_logged_not_raised(self, mock_urlopen):
        # urlopen бросает URLError → notify не должен падать,
        # ошибка должна быть в логе.
        mock_urlopen.side_effect = urllib.error.URLError("network down")

        n = TelegramNotifier(token="t", chat_id="c")
        with self.assertLogs("hope_hash", level="WARNING") as cm:
            n.notify("will fail")
            n.shutdown()

        # В логах есть упоминание ошибки отправки.
        self.assertTrue(
            any("ошибка отправки" in line for line in cm.output),
            f"expected error log, got: {cm.output}",
        )

    @patch("hope_hash.notifier.urllib.request.urlopen")
    def test_worker_survives_failure_and_continues(self, mock_urlopen):
        # После одного сбоя следующее сообщение должно уйти — нить не падает.
        mock_urlopen.side_effect = [
            urllib.error.URLError("transient"),
            _make_mock_response(),
            _make_mock_response(),
        ]

        n = TelegramNotifier(token="t", chat_id="c")
        n.notify("first-fails")
        n.notify("second-ok")
        n.notify("third-ok")
        n.shutdown()

        # Все три попытки сделаны, нить не умерла на первой.
        self.assertEqual(mock_urlopen.call_count, 3)

    @patch("hope_hash.notifier.urllib.request.urlopen")
    def test_http_error_status_raised_internally(self, mock_urlopen):
        # status >= 400 без HTTPError-исключения — наш код должен сам кинуть RuntimeError,
        # которое поймает воркер и залогирует.
        mock_urlopen.return_value = _make_mock_response(status=500)

        n = TelegramNotifier(token="t", chat_id="c")
        with self.assertLogs("hope_hash", level="WARNING") as cm:
            n.notify("oops")
            n.shutdown()

        self.assertTrue(
            any("ошибка отправки" in line for line in cm.output),
            f"expected error log on 500, got: {cm.output}",
        )

    @patch("hope_hash.notifier.urllib.request.urlopen")
    def test_queue_full_drops_message(self, mock_urlopen):
        # Проверяем что переполнение очереди → сообщение отбрасывается с warning.
        # Чтобы не зависеть от расписания воркера, тестируем put_nowait напрямую:
        # заполняем очередь руками, затем убеждаемся что notify() ловит queue.Full.
        import threading as _t

        gate_in = _t.Event()
        gate_release = _t.Event()

        def blocking_open(*a, **kw):
            # Сигналим тесту что воркер уже занят первым сообщением,
            # затем висим до явного релиза.
            gate_in.set()
            gate_release.wait(timeout=3.0)
            return _make_mock_response()

        mock_urlopen.side_effect = blocking_open

        n = TelegramNotifier(token="t", chat_id="c", queue_maxsize=2)
        # Первое уходит в воркер: ждём подтверждения что он застрял в urlopen.
        n.notify("a")
        self.assertTrue(gate_in.wait(timeout=2.0), "worker did not start")
        # Теперь воркер сидит в blocking_open, очередь пуста.
        # Заполняем её до maxsize=2 — оба put должны пройти.
        n.notify("b")
        n.notify("c")
        # Третий — гарантированно уже не влезет, ловим warning.
        with self.assertLogs("hope_hash", level="WARNING") as cm:
            n.notify("d-overflow")

        self.assertTrue(
            any("переполнена" in line for line in cm.output),
            f"expected overflow warning, got: {cm.output}",
        )
        # Освобождаем воркер и корректно завершаем.
        gate_release.set()
        n.shutdown()

    @patch("hope_hash.notifier.urllib.request.urlopen")
    def test_lifecycle_messages(self, mock_urlopen):
        # Smoke-тест на все lifecycle-методы: started/stopped/disconnected/reconnected.
        mock_urlopen.return_value = _make_mock_response()

        n = TelegramNotifier(token="t", chat_id="c")
        n.notify_started("bc1qexampleaddr", "worker-7")
        n.notify_disconnected("connection reset")
        n.notify_reconnected()
        n.notify_stopped()
        n.shutdown()

        self.assertEqual(mock_urlopen.call_count, 4)
        # Проверяем что worker-name попал в первое сообщение.
        first_body = mock_urlopen.call_args_list[0][0][0].data.decode("utf-8")
        self.assertIn("worker-7", first_body)


if __name__ == "__main__":
    unittest.main()
