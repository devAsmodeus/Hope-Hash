"""Stratum V1 клиент: TCP-сокет, JSON line-delimited, обработка mining.* сообщений."""

import json
import socket
import threading

from ._logging import logger


class StratumClient:
    def __init__(self, host: str, port: int, btc_address: str, worker_name: str = "py01",
                 stop_event: threading.Event = None):
        self.host = host
        self.port = port
        self.username = f"{btc_address}.{worker_name}"
        self.sock = None
        self.buf = b""
        self.req_id = 0
        self.extranonce1 = ""
        self.extranonce2_size = 0
        self.difficulty = 1.0
        self.current_job = None
        self.job_lock = threading.Lock()
        # Общий флаг остановки: даёт reader_loop и mine() согласованно завершаться,
        # чтобы при ошибке в одной нити вторая не «висла» молча.
        self.stop_event = stop_event if stop_event is not None else threading.Event()

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=30)
        logger.info(f"[net] подключён к {self.host}:{self.port}")

    def _send(self, method: str, params: list) -> int:
        self.req_id += 1
        msg = json.dumps({"id": self.req_id, "method": method, "params": params}) + "\n"
        self.sock.sendall(msg.encode())
        return self.req_id

    def _recv_line(self) -> str:
        while b"\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("pool закрыл соединение")
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line.decode().strip()

    def subscribe_and_authorize(self):
        sub_id = self._send("mining.subscribe", ["py-solo-miner/0.1"])
        # Ответ на subscribe может прийти не первым — читаем до победы.
        while True:
            msg = json.loads(self._recv_line())
            if msg.get("id") == sub_id and msg.get("result"):
                # result = [[(method, sub_id), ...], extranonce1_hex, extranonce2_size]
                self.extranonce1 = msg["result"][1]
                self.extranonce2_size = msg["result"][2]
                logger.info(
                    f"[stratum] subscribed: extranonce1={self.extranonce1}, "
                    f"en2_size={self.extranonce2_size}"
                )
                break
            self._handle_message(msg)
        self._send("mining.authorize", [self.username, "x"])
        logger.info(f"[stratum] authorize отправлен для воркера {self.username}")

    def _handle_message(self, msg: dict):
        method = msg.get("method")
        params = msg.get("params", []) or []

        if method == "mining.set_difficulty":
            self.difficulty = float(params[0])
            logger.info(f"[stratum] новая сложность: {self.difficulty}")

        elif method == "mining.set_extranonce":
            # Пул может «на лету» сменить extranonce1 (например, при ребалансе воркеров).
            # Старый job становится невалидным: extranonce2 теперь компонуется иначе,
            # поэтому сбрасываем current_job — mine() подождёт ближайший mining.notify.
            with self.job_lock:
                self.extranonce1 = params[0]
                self.extranonce2_size = int(params[1])
                self.current_job = None
            logger.info(
                f"[stratum] новая extranonce1={self.extranonce1}, "
                f"en2_size={self.extranonce2_size} (job сброшен)"
            )

        elif method == "mining.notify":
            with self.job_lock:
                self.current_job = {
                    "job_id":        params[0],
                    "prevhash":      params[1],
                    "coinb1":        params[2],
                    "coinb2":        params[3],
                    "merkle_branch": params[4],
                    "version":       params[5],
                    "nbits":         params[6],
                    "ntime":         params[7],
                    "clean":         params[8],
                }
            logger.info(f"[stratum] новая работа job_id={params[0]} clean={params[8]}")

        elif msg.get("id") and "result" in msg:
            if msg["result"] is True:
                logger.info(f"[stratum] *** ШАР ПРИНЯТ *** (id={msg['id']})")
            elif msg.get("error"):
                logger.warning(f"[stratum] ошибка: {msg['error']}")

    def reader_loop(self):
        """
        Фоновая нить, постоянно слушает сообщения от пула.
        Выходит при ошибке сети или при выставленном stop_event — главное,
        не «умирать тихо», иначе mine() будет крутить уже невалидную работу.
        """
        while not self.stop_event.is_set():
            try:
                line = self._recv_line()
                if line:
                    self._handle_message(json.loads(line))
            except (ConnectionError, socket.error, OSError) as e:
                if self.stop_event.is_set():
                    return
                logger.warning(f"[net] ошибка чтения: {e}")
                return
            except json.JSONDecodeError as e:
                # Битая строка — не повод ронять соединение, просто скипаем.
                logger.warning(f"[net] битый JSON от пула: {e}")
                continue

    def submit(self, job_id, extranonce2, ntime, nonce_hex):
        self._send("mining.submit", [self.username, job_id, extranonce2, ntime, nonce_hex])

    def close(self):
        """Аккуратно гасим сокет: recv() в reader_loop разблокируется и нить выйдет."""
        try:
            if self.sock is not None:
                self.sock.close()
        except OSError:
            pass
