"""Тесты таймингов notify_share_accepted: пинг летит ТОЛЬКО после ack пула,
не из submit-пути.

Это регресс-тест на класс ошибок «уведомили о принятом шаре, а пул его потом
отверг». Архитектура mine() теперь ждёт callback on_share_result, и
notify_share_accepted дёргается только при accepted=True от пула.

Прямой интеграционный тест mine() — слишком тяжёлый (multiprocessing pool,
сетевой клиент). Вместо этого тестируем через StratumClient:

1. Ставим callback on_share_result, который дергает notifier.
2. Симулируем submit() (запоминает req_id).
3. Симулируем входящее ack-сообщение reader_loop'а через _handle_message.
4. Убеждаемся что notifier дёрнулся ровно один раз.
5. Симулируем reject — notifier НЕ должен быть дёрнут (только accepted).
"""

import unittest
from unittest.mock import MagicMock

from hope_hash.notifier import TelegramNotifier
from hope_hash.stratum import StratumClient


class _FakeSocket:
    """Минимальный fake-socket: запоминает посланное, возвращает заданное."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._recv_chunks: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, n: int) -> bytes:
        if not self._recv_chunks:
            return b""
        return self._recv_chunks.pop(0)

    def close(self) -> None:
        pass


class TestNotifyTiming(unittest.TestCase):
    """Notify_share_accepted дёргается только из ack-callback."""

    def _make_client_with_notifier(self) -> tuple[StratumClient, MagicMock]:
        client = StratumClient("h", 1, "bc1qaddress", "w")
        client.sock = _FakeSocket()  # type: ignore[assignment]

        # Mock notifier — мы тестируем именно тайминг вызова, не реальную сеть.
        notifier = MagicMock(spec=TelegramNotifier)
        notifier.enabled = True

        # Воспроизводим логику из mine(): один callback регистрируется,
        # он отвечает за все последствия ack-а.
        def on_share_result(req_id: int, accepted: bool) -> None:
            if accepted:
                notifier.notify_share_accepted(job_id="job-x", difficulty=1.0)

        client.on_share_result = on_share_result
        return client, notifier

    def test_no_notify_on_submit_alone(self) -> None:
        # Сценарий: submit ушёл, ответ ещё не пришёл. notifier должен молчать.
        client, notifier = self._make_client_with_notifier()

        client.submit("job-x", "00000001", "5e000000", "deadbeef")

        notifier.notify_share_accepted.assert_not_called()

    def test_notify_only_after_pool_ack_accepted(self) -> None:
        # Submit → ack(accepted=true) → ровно один notify.
        client, notifier = self._make_client_with_notifier()

        req_id = client.submit("job-x", "00000001", "5e000000", "deadbeef")

        # Симулируем ответ пула: сообщение с тем же id и result=true.
        client._handle_message({"id": req_id, "result": True})

        notifier.notify_share_accepted.assert_called_once_with(
            job_id="job-x", difficulty=1.0
        )

    def test_no_notify_on_pool_reject(self) -> None:
        # Submit → ack(result=false) → notifier НЕ должен быть вызван.
        client, notifier = self._make_client_with_notifier()

        req_id = client.submit("job-x", "00000001", "5e000000", "deadbeef")
        client._handle_message({"id": req_id, "result": False, "error": "Low difficulty share"})

        notifier.notify_share_accepted.assert_not_called()

    def test_notify_exactly_once_on_duplicate_ack(self) -> None:
        # Если по какой-то причине пул присылает ack дважды (или callback
        # перевызвался) — notifier должен быть вызван ровно один раз,
        # т.к. submit_req_ids вычищает себя в _handle_message.
        client, notifier = self._make_client_with_notifier()

        req_id = client.submit("job-x", "00000001", "5e000000", "deadbeef")

        client._handle_message({"id": req_id, "result": True})
        client._handle_message({"id": req_id, "result": True})  # дубль

        notifier.notify_share_accepted.assert_called_once()

    def test_notify_for_each_distinct_share(self) -> None:
        # Каждая принятая шара = отдельный вызов notifier.
        client, notifier = self._make_client_with_notifier()

        rid1 = client.submit("job-x", "00000001", "5e000000", "deadbeef")
        rid2 = client.submit("job-x", "00000002", "5e000000", "deadbef0")

        client._handle_message({"id": rid1, "result": True})
        client._handle_message({"id": rid2, "result": True})

        self.assertEqual(notifier.notify_share_accepted.call_count, 2)


if __name__ == "__main__":
    unittest.main()
