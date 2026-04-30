"""Тесты протокольного слоя StratumClient (без реальной сети).

Подменяем `socket.create_connection` на FakeSocket, который проигрывает
заранее записанные line-delimited JSON-сообщения и собирает всё, что
клиент отправляет — потом ассерт на этой записи.

Покрываем:
- subscribe: парсинг extranonce1/en2_size, чтение out-of-order ответов
- authorize: success → ConnectionError при пуловом отказе
- set_difficulty / set_extranonce / mining.notify
- submit: отслеживание req_id, callback on_share_result для accept/reject
- reader_loop: устойчивость к битому JSON, остановка по stop_event
- close() идемпотентен
- suggest_difficulty: отправляется автоматически после авторизации
"""

import json
import socket
import threading
import unittest
from typing import Iterable
from unittest.mock import patch

from hope_hash.stratum import StratumClient


# ───────────────────────── FakeSocket ─────────────────────────

class FakeSocket:
    """Имитирует TCP-сокет с заранее заданным сценарием server-сообщений.

    `incoming` — итератор строк (без \\n), которые будут отдаваться по recv()
    в порядке поступления. Когда они кончатся, recv() блокируется до close()
    или возвращает b"" (что StratumClient трактует как «pool закрыл»).

    `outbox` — все отправленные клиентом строки (decoded, без \\n).
    """

    def __init__(self, incoming: Iterable[str]):
        self._incoming = list(incoming)
        self._idx = 0
        self.outbox: list[str] = []
        self._closed = False
        # На каждое recv() ждём, пока появится следующий чанк или закроют сокет.
        self._cond = threading.Condition()
        # Буфер незабранных байт (incoming сериализованный с \n).
        self._pending = b""
        for line in self._incoming:
            self._pending += line.encode() + b"\n"

    def sendall(self, data: bytes) -> None:
        if self._closed:
            raise OSError("socket is closed")
        # Клиент шлёт построчно — собираем по \n для удобства ассертов.
        text = data.decode()
        for line in text.split("\n"):
            if line:
                self.outbox.append(line)

    def recv(self, n: int) -> bytes:
        with self._cond:
            while not self._pending and not self._closed:
                # Простой блок: тесты сами должны кормить через push_line() или close().
                self._cond.wait(timeout=0.5)
                # Если ничего не появилось за таймаут — выходим как «соединение закрыто».
                if not self._pending and not self._closed:
                    return b""
            if not self._pending and self._closed:
                return b""
            chunk = self._pending[:n]
            self._pending = self._pending[n:]
            return chunk

    def push_line(self, line: str) -> None:
        """Подкинуть серверное сообщение в реальном времени (для reader_loop)."""
        with self._cond:
            self._pending += line.encode() + b"\n"
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


def _make_client(fake: FakeSocket, **kwargs) -> StratumClient:
    """Создаёт клиента с подменённым socket.create_connection → fake."""
    client = StratumClient("test.pool", 1234, "bc1qtest", "py01", **kwargs)
    with patch("socket.create_connection", return_value=fake):
        client.connect()
    return client


# ───────────────────────── subscribe + authorize ─────────────────────────

class TestSubscribeAuthorize(unittest.TestCase):

    def test_subscribe_parses_extranonce(self):
        fake = FakeSocket([
            json.dumps({"id": 1, "result": [[["mining.notify", "abc"]], "ab12cd34", 4]}),
            json.dumps({"id": 2, "result": True}),
        ])
        client = _make_client(fake)
        client.subscribe_and_authorize()
        self.assertEqual(client.extranonce1, "ab12cd34")
        self.assertEqual(client.extranonce2_size, 4)
        # Проверяем, что отправили subscribe + authorize в правильном порядке.
        sent = [json.loads(x) for x in fake.outbox]
        self.assertEqual(sent[0]["method"], "mining.subscribe")
        self.assertEqual(sent[1]["method"], "mining.authorize")
        self.assertEqual(sent[1]["params"][0], "bc1qtest.py01")

    def test_subscribe_handles_out_of_order_messages(self):
        # Пул может прислать set_difficulty до ответа на subscribe — клиент
        # должен это применить и продолжить ждать свой id.
        fake = FakeSocket([
            json.dumps({"method": "mining.set_difficulty", "params": [2.5]}),
            json.dumps({"id": 1, "result": [[], "deadbeef", 4]}),
            json.dumps({"id": 2, "result": True}),
        ])
        client = _make_client(fake)
        client.subscribe_and_authorize()
        self.assertEqual(client.extranonce1, "deadbeef")
        self.assertEqual(client.difficulty, 2.5)

    def test_authorize_rejection_raises(self):
        fake = FakeSocket([
            json.dumps({"id": 1, "result": [[], "ab", 4]}),
            json.dumps({"id": 2, "result": False, "error": "bad worker"}),
        ])
        client = _make_client(fake)
        with self.assertRaises(ConnectionError) as ctx:
            client.subscribe_and_authorize()
        self.assertIn("authorize", str(ctx.exception))

    def test_suggest_diff_sent_after_authorize(self):
        fake = FakeSocket([
            json.dumps({"id": 1, "result": [[], "ab", 4]}),
            json.dumps({"id": 2, "result": True}),
        ])
        client = _make_client(fake, suggest_diff=0.001)
        client.subscribe_and_authorize()
        sent = [json.loads(x) for x in fake.outbox]
        methods = [m["method"] for m in sent]
        self.assertIn("mining.suggest_difficulty", methods)
        idx = methods.index("mining.suggest_difficulty")
        self.assertEqual(sent[idx]["params"], [0.001])


# ───────────────────────── _handle_message ─────────────────────────

class TestHandleMessage(unittest.TestCase):

    def setUp(self):
        # Минимальный клиент без сети — _handle_message работает на dict'ах.
        self.client = StratumClient("h", 1, "bc1qtest", "py01")

    def test_set_difficulty_updates_under_lock(self):
        self.client._handle_message({"method": "mining.set_difficulty", "params": [42.0]})
        self.assertEqual(self.client.difficulty, 42.0)

    def test_set_extranonce_resets_job(self):
        # Сначала «положим» job, потом set_extranonce должен его сбросить.
        self.client._handle_message({
            "method": "mining.notify",
            "params": ["j1", "p", "c1", "c2", [], "v", "n", "t", True],
        })
        self.assertIsNotNone(self.client.current_job)
        self.client._handle_message({
            "method": "mining.set_extranonce",
            "params": ["fffefdfc", 8],
        })
        self.assertEqual(self.client.extranonce1, "fffefdfc")
        self.assertEqual(self.client.extranonce2_size, 8)
        self.assertIsNone(self.client.current_job)

    def test_notify_populates_all_fields(self):
        params = ["job1", "prevhashhex", "coinb1hex", "coinb2hex",
                  ["branch1", "branch2"], "20000000", "1d00ffff", "5fa1b2c3", True]
        self.client._handle_message({"method": "mining.notify", "params": params})
        job = self.client.current_job
        self.assertEqual(job["job_id"], "job1")
        self.assertEqual(job["merkle_branch"], ["branch1", "branch2"])
        self.assertTrue(job["clean"])

    def test_submit_response_triggers_callback_accepted(self):
        results: list[tuple[int, bool]] = []
        self.client.on_share_result = lambda req, ok: results.append((req, ok))

        # Регистрируем req_id как submit (как сделал бы submit()).
        with self.client._submit_lock:
            self.client._submit_req_ids.add(7)
        self.client._handle_message({"id": 7, "result": True})
        self.assertEqual(results, [(7, True)])
        # req_id должен быть «съеден», повторный ответ ничего не делает.
        self.client._handle_message({"id": 7, "result": True})
        self.assertEqual(len(results), 1)

    def test_submit_response_rejected(self):
        results: list[tuple[int, bool]] = []
        self.client.on_share_result = lambda req, ok: results.append((req, ok))
        with self.client._submit_lock:
            self.client._submit_req_ids.add(9)
        self.client._handle_message({
            "id": 9, "result": False, "error": [23, "Low difficulty share", None]
        })
        self.assertEqual(results, [(9, False)])

    def test_non_submit_id_response_does_not_call_callback(self):
        # Ответ на subscribe/authorize/suggest_difficulty — не должен дёргать колбэк.
        called: list = []
        self.client.on_share_result = lambda req, ok: called.append((req, ok))
        self.client._handle_message({"id": 1, "result": True})
        self.assertEqual(called, [])


# ───────────────────────── submit ─────────────────────────

class TestSubmit(unittest.TestCase):

    def test_submit_tracks_req_id(self):
        fake = FakeSocket([
            json.dumps({"id": 1, "result": [[], "ab", 4]}),
            json.dumps({"id": 2, "result": True}),
        ])
        client = _make_client(fake)
        client.subscribe_and_authorize()
        req_id = client.submit("jobX", "00000001", "5fa1b2c3", "deadbeef")
        # _submit_req_ids должен содержать только что отправленный id.
        with client._submit_lock:
            self.assertIn(req_id, client._submit_req_ids)
        # Проверяем содержимое отправленного submit.
        last = json.loads(fake.outbox[-1])
        self.assertEqual(last["method"], "mining.submit")
        self.assertEqual(last["params"], ["bc1qtest.py01", "jobX", "00000001",
                                          "5fa1b2c3", "deadbeef"])


# ───────────────────────── reader_loop ─────────────────────────

class TestReaderLoop(unittest.TestCase):

    def test_reader_loop_processes_notify_then_stops(self):
        fake = FakeSocket([])
        client = _make_client(fake)
        thr = threading.Thread(target=client.reader_loop, daemon=True)
        thr.start()
        # Подкидываем серверное сообщение в живом режиме.
        fake.push_line(json.dumps({
            "method": "mining.notify",
            "params": ["jobZ", "p", "c1", "c2", [], "v", "n", "t", True],
        }))
        # Даём reader_loop время на обработку.
        for _ in range(50):
            if client.current_job is not None:
                break
            threading.Event().wait(0.02)
        self.assertIsNotNone(client.current_job)
        self.assertEqual(client.current_job["job_id"], "jobZ")

        # Останавливаем через stop_event + close — нить должна выйти за разумное время.
        client.stop_event.set()
        client.close()
        thr.join(timeout=2)
        self.assertFalse(thr.is_alive())

    def test_reader_loop_survives_bad_json(self):
        fake = FakeSocket([])
        client = _make_client(fake)
        thr = threading.Thread(target=client.reader_loop, daemon=True)
        thr.start()
        fake.push_line("{not json")              # битая строка — должна быть скипнута
        fake.push_line(json.dumps({"method": "mining.set_difficulty", "params": [3.0]}))
        for _ in range(50):
            if client.difficulty == 3.0:
                break
            threading.Event().wait(0.02)
        self.assertEqual(client.difficulty, 3.0)
        client.stop_event.set()
        client.close()
        thr.join(timeout=2)


# ───────────────────────── lifecycle ─────────────────────────

class TestLifecycle(unittest.TestCase):

    def test_close_is_idempotent(self):
        fake = FakeSocket([])
        client = _make_client(fake)
        client.close()
        client.close()  # второй вызов не должен бросать

    def test_close_without_connect(self):
        client = StratumClient("h", 1, "bc1qtest")
        client.close()  # sock is None — должно молча проглотиться


if __name__ == "__main__":
    unittest.main()
