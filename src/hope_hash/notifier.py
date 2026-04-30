"""Telegram-уведомления через stdlib urllib. Без зависимостей.

Использование:
    notifier = TelegramNotifier(token="123:ABC", chat_id="456789")
    notifier.notify("Майнер стартовал")
    notifier.notify_share_accepted(job_id="abc", difficulty=1.0)
    notifier.notify_block_found(hash_hex="00000000...", height=999999)
    notifier.shutdown()  # дренирует очередь, закрывает воркер-нить

Если token или chat_id не заданы — все методы становятся no-op (silent disable).
Это позволяет интегрировать notifier в miner без обязательной настройки.

Архитектура: фоновая нить-воркер тащит сообщения из `queue.Queue` и шлёт
на Telegram Bot API через urllib. Сетевые вызовы не блокируют hot-path
майнера; при переполнении очереди сообщения отбрасываются с warning.
"""

import logging
import os
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger("hope_hash")

# Шаблон URL Telegram Bot API. sendMessage принимает form-encoded body.
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


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

    def shutdown(self, timeout: float = 5.0) -> None:
        """Дренирует очередь и останавливает воркер. Идемпотентно."""
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
