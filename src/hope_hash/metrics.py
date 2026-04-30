"""Prometheus-совместимые метрики через stdlib http.server. Без зависимостей.

Два класса: ``Metrics`` — потокобезопасный регистр counter/gauge,
``MetricsServer`` — HTTP-сервер ``/metrics`` на фоновой нити. Логгер
берём по имени пакета, чтобы не плодить циклических импортов.
"""

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("hope_hash")


# Допустимые символы для имени метрики по Prometheus naming convention:
# первая буква — [a-zA-Z_:], остальные — [a-zA-Z0-9_:]. Всё прочее
# заменяем на подчёркивание. Это терпит произвольный пользовательский ввод.
_NAME_FIRST_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_:")
_NAME_REST_OK = _NAME_FIRST_OK | set("0123456789")


def _sanitize_name(name: str) -> str:
    """Приводит имя метрики к виду ``[a-zA-Z_:][a-zA-Z0-9_:]*``."""
    if not name:
        return "_"
    chars = []
    for i, ch in enumerate(name):
        ok = _NAME_FIRST_OK if i == 0 else _NAME_REST_OK
        chars.append(ch if ch in ok else "_")
    return "".join(chars)


def _format_float(v: float) -> str:
    """Форматирует gauge для Prometheus: целые без ``.0``, дробные через ``repr``."""
    if v == int(v) and abs(v) < 1e16:
        return str(int(v))
    return repr(v)


class Metrics:
    """Регистр метрик. Потокобезопасный.

    Поддерживаемые типы:

    - counter — монотонно растущий int (например, число шар).
    - gauge — произвольное float (например, текущий хешрейт).

    Формат вывода соответствует Prometheus text format 0.0.4. Помимо
    пользовательских метрик, ``render()`` всегда добавляет автоматический
    gauge ``hopehash_uptime_seconds``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._help: dict[str, str] = {}
        self._created_at = time.time()

    def counter_inc(self, name: str, value: int = 1, help: str | None = None) -> None:
        """Инкрементирует counter. ``value`` должен быть >= 0."""
        if value < 0:
            # Counter по определению монотонный; отрицательный шаг — это баг
            # в коде вызывающего, лучше упасть громко, чем тихо «декрементить».
            raise ValueError("counter_inc: value must be >= 0")
        key = _sanitize_name(name)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + int(value)
            if help is not None:
                self._help[key] = help

    def gauge_set(self, name: str, value: float, help: str | None = None) -> None:
        """Устанавливает gauge в указанное значение."""
        key = _sanitize_name(name)
        with self._lock:
            self._gauges[key] = float(value)
            if help is not None:
                self._help[key] = help

    def render(self) -> bytes:
        """Возвращает все метрики в Prometheus text format. UTF-8 bytes."""
        # Снимок под локом — потом форматируем без удержания лока, чтобы
        # не блокировать producer'ов на длительный sprintf.
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            helps = dict(self._help)
            uptime = time.time() - self._created_at

        lines: list[str] = []

        for name in sorted(counters):
            help_text = helps.get(name, f"Counter {name}")
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {counters[name]}")

        for name in sorted(gauges):
            help_text = helps.get(name, f"Gauge {name}")
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {_format_float(gauges[name])}")

        # Автоматический uptime — всегда последним, чтобы порядок был стабильным.
        lines.append("# HELP hopehash_uptime_seconds Seconds since metrics registry created")
        lines.append("# TYPE hopehash_uptime_seconds gauge")
        lines.append(f"hopehash_uptime_seconds {_format_float(uptime)}")

        # Финальный перевод строки — Prometheus exposition требует, чтобы
        # последняя метрика заканчивалась ``\n``.
        return ("\n".join(lines) + "\n").encode("utf-8")


def _make_handler(metrics: Metrics) -> type[BaseHTTPRequestHandler]:
    """Фабрика handler-класса с ``metrics`` через замыкание — без глобалов."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — имя задано базовым классом
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = metrics.render()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            # Подавляем стандартный stderr-лог BaseHTTPRequestHandler:
            # в проекте единый канал — logger "hope_hash", и засорять его
            # каждым GET /metrics не нужно.
            return

    return _Handler


class MetricsServer:
    """HTTP-сервер для ``/metrics`` на отдельной нити. Старт/стоп идемпотентны."""

    def __init__(self, metrics: Metrics, host: str = "127.0.0.1", port: int = 9090) -> None:
        self.metrics = metrics
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        # Лок защищает start/stop от гонки, если их зовут из разных нитей.
        self._lifecycle_lock = threading.Lock()

    def start(self) -> None:
        """Запускает сервер в фоновой нити. Идемпотентен."""
        with self._lifecycle_lock:
            if self._server is not None:
                # Уже запущен — ничего не делаем, чтобы не порвать рабочий
                # сокет повторным bind'ом.
                return
            handler_cls = _make_handler(self.metrics)
            self._server = ThreadingHTTPServer((self.host, self.port), handler_cls)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name=f"hope_hash-metrics-{self.port}",
                daemon=True,
            )
            self._thread.start()
            logger.info("[metrics] сервер запущен на %s", self.url)

    def stop(self, timeout: float = 2.0) -> None:
        """Останавливает сервер. Идемпотентен."""
        with self._lifecycle_lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None

        if server is not None:
            # shutdown() блокирует serve_forever и ждёт его выхода,
            # server_close() закрывает listening-сокет.
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=timeout)
        if server is not None:
            logger.info("[metrics] сервер остановлен")

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/metrics"
