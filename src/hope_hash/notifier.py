"""Telegram-уведомления через stdlib urllib. Без зависимостей.

Использование:
    notifier = TelegramNotifier(token="123:ABC", chat_id="456789")
    notifier.notify("Майнер стартовал")
    notifier.notify_share_accepted(job_id="abc", difficulty=1.0)
    notifier.notify_block_found(hash_hex="00000000...", height=999999)
    notifier.shutdown()  # дренирует очередь, закрывает воркер-нить

Если token или chat_id не заданы — все методы становятся no-op (silent disable).
Это позволяет интегрировать notifier в miner без обязательной настройки.

Архитектура исходящих: фоновая нить-воркер тащит сообщения из ``queue.Queue``
и шлёт на Telegram Bot API через urllib. Сетевые вызовы не блокируют
hot-path майнера; при переполнении очереди сообщения отбрасываются с warning.

Архитектура входящих (опционально, ``HOPE_HASH_TELEGRAM_INBOUND=1``):
вторая фоновая нить long-polling-ом читает getUpdates и диспатчит
``/stats``, ``/stop``, ``/restart``. Команды принимаются ТОЛЬКО от
``chat_id``, на который настроен notifier — это закрывает основной
authz-вектор (бот может быть добавлен в чужой чат).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger("hope_hash")

# Шаблон URL Telegram Bot API. sendMessage принимает form-encoded body.
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_GET_UPDATES = "https://api.telegram.org/bot{token}/getUpdates"

# Поддерживаемые inbound-команды.
KNOWN_COMMANDS: tuple[str, ...] = ("/stats", "/stop", "/restart", "/help", "/start")


class TelegramNotifier:
    """Асинхронный отправщик сообщений в Telegram.

    Работает через background thread + очередь, чтобы сетевые вызовы
    не блокировали майнинг-цикл. Если token или chat_id отсутствуют
    (None или пустая строка) — все методы превращаются в no-op.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        timeout: float = 5.0,
        queue_maxsize: int = 100,
    ):
        # Disabled mode: явно проверяем оба значения. Пустая строка трактуется
        # как «не задано» — это удобнее, чем падать на пустом env-var.
        self.enabled = bool(token) and bool(chat_id)
        self.token = token or ""
        self.chat_id = chat_id or ""
        self.timeout = timeout
        self._queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # ─── inbound state (опционально) ───
        # Поллер-нить и реестр обработчиков команд. Включается через
        # start_inbound() — по умолчанию выключено, чтобы не открывать
        # сетевую дыру без явного opt-in.
        self._inbound_thread: Optional[threading.Thread] = None
        self._inbound_stop = threading.Event()
        self._command_handlers: dict[str, Callable[[], Optional[str]]] = {}
        self._last_update_id: int = 0
        # Lock защищает _last_update_id и _command_handlers — обновляются
        # из inbound-нити, читаются из API-методов.
        self._inbound_lock = threading.Lock()

        if self.enabled:
            # daemon=True — чтобы при жёстком SIGKILL процесс не висел из-за нити.
            # Корректное завершение всё равно через shutdown().
            self._thread = threading.Thread(
                target=self._worker,
                daemon=True,
                name="telegram-notifier",
            )
            self._thread.start()
            logger.info("[telegram] notifier активен (chat=%s)", self.chat_id)
        else:
            logger.info("[telegram] notifier отключён (нет token/chat_id)")

    @classmethod
    def from_env(cls) -> "TelegramNotifier":
        """Читает HOPE_HASH_TELEGRAM_TOKEN и HOPE_HASH_TELEGRAM_CHAT_ID из env."""
        return cls(
            token=os.environ.get("HOPE_HASH_TELEGRAM_TOKEN"),
            chat_id=os.environ.get("HOPE_HASH_TELEGRAM_CHAT_ID"),
        )

    @staticmethod
    def inbound_enabled_in_env() -> bool:
        """True если HOPE_HASH_TELEGRAM_INBOUND=1 в окружении."""
        return os.environ.get("HOPE_HASH_TELEGRAM_INBOUND", "").strip() in ("1", "true", "yes", "on")

    def notify(self, text: str) -> None:
        """Кладёт сообщение в очередь. No-op если disabled или очередь полна."""
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            # Сознательно роняем сообщение, а не блокируем — для майнера
            # потеря уведомления безопаснее, чем застывший hot-path.
            logger.warning("[telegram] очередь переполнена, сообщение отброшено")

    def notify_started(self, btc_address: str, worker_name: str) -> None:
        self.notify(
            f"🟢 Hope-Hash запущен\nworker: {worker_name}\nadr: {btc_address[:8]}…"
        )

    def notify_stopped(self) -> None:
        self.notify("🔴 Hope-Hash остановлен")

    def notify_disconnected(self, reason: str) -> None:
        self.notify(f"⚠️ Потеря соединения: {reason}")

    def notify_reconnected(self) -> None:
        self.notify("✅ Соединение восстановлено")

    def notify_share_accepted(self, job_id: str, difficulty: float) -> None:
        self.notify(f"✓ Шар принят (job={job_id}, diff={difficulty})")

    def notify_block_found(self, hash_hex: str, height: Optional[int] = None) -> None:
        h = f" #{height}" if height else ""
        self.notify(f"🎉 БЛОК НАЙДЕН{h}!\nhash: {hash_hex[:32]}…")

    # ─────────────────── inbound long-poll ───────────────────

    def register_command(self, name: str, handler: Callable[[], Optional[str]]) -> None:
        """Регистрирует обработчик команды.

        ``handler`` вызывается в inbound-нити (не в hot-path майнера).
        Возвращает строку — она будет отправлена в чат как ack; ``None`` —
        ничего не отвечаем (но факт обработки логгируется).
        """
        with self._inbound_lock:
            self._command_handlers[name] = handler

    def start_inbound(self, poll_interval: float = 25.0) -> bool:
        """Поднимает нить-поллер getUpdates. Возвращает True если стартанула.

        Idempotent: повторный вызов — no-op. ``poll_interval`` — long-poll
        timeout, который мы передаём Telegram'у; реальный getUpdates висит
        до этой длительности или до прихода события.
        """
        if not self.enabled:
            logger.info("[telegram] inbound не запущен: notifier disabled")
            return False
        if self._inbound_thread is not None and self._inbound_thread.is_alive():
            return True
        self._inbound_stop.clear()
        self._inbound_thread = threading.Thread(
            target=self._inbound_loop,
            args=(poll_interval,),
            daemon=True,
            name="telegram-inbound",
        )
        self._inbound_thread.start()
        logger.info("[tg] inbound long-poll запущен (chat=%s)", self.chat_id)
        return True

    def stop_inbound(self, timeout: float = 5.0) -> None:
        """Останавливает inbound-нить. Идемпотентно."""
        self._inbound_stop.set()
        t = self._inbound_thread
        self._inbound_thread = None
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    def _inbound_loop(self, poll_interval: float) -> None:
        # Backoff на сетевые сбои, чтобы не спамить API при выключенном роутере.
        backoff = 1.0
        while not self._inbound_stop.is_set():
            try:
                updates = self._fetch_updates(poll_interval)
                backoff = 1.0
                for upd in updates:
                    self._handle_update(upd)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                logger.warning("[tg] long-poll сетевая ошибка: %s (retry %.1fs)", e, backoff)
                self._inbound_stop.wait(timeout=backoff)
                backoff = min(backoff * 2, 60.0)
            except (ValueError, json.JSONDecodeError) as e:
                # Битый JSON — уж точно не наше дело его парсить, скипаем.
                logger.warning("[tg] битый ответ getUpdates: %s", e)
                self._inbound_stop.wait(timeout=1.0)

    def _fetch_updates(self, poll_interval: float) -> list[dict]:
        """Один HTTP-вызов getUpdates с long-poll timeout."""
        with self._inbound_lock:
            offset = self._last_update_id + 1 if self._last_update_id else 0

        params: dict[str, str | int] = {
            "timeout": int(poll_interval),
            "allowed_updates": json.dumps(["message"]),
        }
        if offset:
            params["offset"] = offset
        url = TELEGRAM_GET_UPDATES.format(token=self.token) + "?" + urllib.parse.urlencode(params)
        # urlopen timeout = poll_interval + 5с (на накладные).
        with urllib.request.urlopen(url, timeout=poll_interval + 5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("ok"):
            logger.warning("[tg] getUpdates вернул ok=false: %s", data.get("description"))
            return []
        return list(data.get("result", []))

    def _handle_update(self, upd: dict) -> None:
        """Один update: фильтр по chat_id, парс команды, диспатч."""
        update_id = upd.get("update_id")
        if update_id is not None:
            with self._inbound_lock:
                if update_id > self._last_update_id:
                    self._last_update_id = update_id

        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        # Telegram возвращает chat_id как int; в env у нас строка — сравниваем строки.
        incoming_chat_id = str(chat.get("id", ""))
        if incoming_chat_id != str(self.chat_id):
            logger.warning(
                "[tg] отвергнут update от чужого chat_id=%r (ожидаем %r)",
                incoming_chat_id, self.chat_id,
            )
            return

        text = (msg.get("text") or "").strip()
        if not text:
            return

        # Команда — первое слово; всё после — аргументы (мы их пока игнорируем).
        cmd = text.split()[0].lower()
        # Telegram-команды могут идти с username-суффиксом: "/stats@MyBot".
        if "@" in cmd:
            cmd = cmd.split("@", 1)[0]

        if cmd not in KNOWN_COMMANDS:
            return

        with self._inbound_lock:
            handler = self._command_handlers.get(cmd)

        if handler is None:
            self.notify(f"команда {cmd} не настроена в этом инстансе")
            return

        logger.info("[tg] выполнение команды %s от chat=%s", cmd, incoming_chat_id)
        try:
            reply = handler()
        except Exception as exc:
            logger.warning("[tg] handler %s упал: %s", cmd, exc)
            self.notify(f"⚠️ ошибка в обработчике {cmd}: {exc}")
            return
        if reply:
            self.notify(reply)

    # ─────────────────── shutdown ───────────────────

    def shutdown(self, timeout: float = 5.0) -> None:
        """Дренирует очередь и останавливает воркер. Идемпотентно."""
        # Inbound стопаем первым: иначе он может попытаться положить ack
        # в уже закрытую очередь.
        self.stop_inbound(timeout=timeout)

        if not self.enabled or self._thread is None:
            return
        # Сначала ждём, пока воркер обработает все ранее положенные сообщения.
        # queue.join() блокируется до тех пор, пока на каждый put не сделан task_done.
        self._queue.join()
        # Теперь сигналим воркеру выйти и пробуждаем его sentinel-ом None.
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            # Очередь заполнена — но раз join вернулся, воркер уже опустошил всё
            # и сейчас ждёт на get(); put_nowait не должен падать. На всякий случай
            # используем put с таймаутом как fallback.
            try:
                self._queue.put(None, timeout=1.0)
            except queue.Full:
                pass
        self._thread.join(timeout=timeout)
        self._thread = None

    def _worker(self) -> None:
        """Фоновая нить: тащит из очереди и шлёт в Telegram API."""
        while True:
            item = self._queue.get()
            try:
                # None — sentinel для остановки. Stop_event дублирует на случай
                # если кто-то выставил флаг без отправки sentinel-а.
                if item is None or self._stop_event.is_set() and item is None:
                    return
                self._send(item)
            except Exception as e:
                # Сетевые сбои/HTTP-ошибки/таймауты — логируем и идём дальше.
                # Не даём упасть нити: иначе следующие notify() будут уходить в пустоту.
                logger.warning("[telegram] ошибка отправки: %s", e)
            finally:
                # task_done обязателен на КАЖДЫЙ get (включая sentinel),
                # иначе queue.join() в shutdown() зависнет.
                self._queue.task_done()

    def _send(self, text: str) -> None:
        """Один HTTP POST на api.telegram.org. urllib + urlencode."""
        url = TELEGRAM_API.format(token=self.token)
        data = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            # urlopen уже бросает HTTPError на 4xx/5xx, но проверим status явно
            # на случай нестандартных кодов.
            if resp.status >= 400:
                raise RuntimeError(f"Telegram API status {resp.status}")
        logger.debug("[telegram] отправлено: %r", text[:60])
