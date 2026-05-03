"""Web-дашборд на stdlib ``http.server``. Без зависимостей и без CDN.

Структура зеркалит ``MetricsServer``: ``ThreadingHTTPServer`` в фоновой
нити, mutable container для health-провайдера, идемпотентные ``start()`` /
``stop()``. Источник данных — общий ``StatsProvider`` (тот же, что у TUI),
поэтому web-страница и curses-дашборд показывают одинаковые числа.

Эндпоинты:

- ``GET /`` — single-page HTML (vanilla JS, без CDN), сам опрашивает
  ``/api/stats`` каждые 2 секунды и подключается к ``/api/events`` для
  стрима.
- ``GET /api/stats`` — JSON-снапшот: hashrate, uptime, шары, pool URL,
  sha_backend, текущий job_id и т. д. ``Cache-Control: no-store``.
- ``GET /api/events`` — Server-Sent Events: ``share_found``,
  ``share_accepted``, ``share_rejected``, ``job``, ``pool``. Keep-alive
  comment каждые 15 секунд.
- ``GET /healthz`` — то же, что у ``MetricsServer`` (ставится через
  ``set_health_provider``). Удобно, когда web и metrics на разных портах.

Web по умолчанию слушает только loopback (``127.0.0.1``) — то же
правило, что и у ``MetricsServer``. Если нужен внешний доступ, оператор
ставит обратный прокси (nginx/caddy) с auth.
"""

from __future__ import annotations

import html
import json
import logging
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

from .tui import StatsProvider, format_rate, format_uptime

logger = logging.getLogger("hope_hash")

HealthProvider = Callable[[], dict]


# Размер кольцевого буфера событий на одного SSE-клиента. Больше — больше
# памяти на медленного клиента; меньше — рискуем уронить событие при флуде.
_SSE_QUEUE_MAX = 256

# Период keepalive-комментариев SSE. Спецификация SSE не требует, но без
# них некоторые прокси (включая nginx default) рвут idle-коннект.
_SSE_KEEPALIVE_S = 15.0


def _stats_payload(provider: StatsProvider) -> dict[str, Any]:
    """Серилизуемый JSON-снапшот для ``/api/stats``.

    Не возвращает datetime/Path/etc — только строки/числа/None, чтобы
    ``json.dumps`` не падал на нестандартных типах.
    """
    snap = provider.snapshot()
    return {
        "hashrate_ema": snap.hashrate_ema,
        "hashrate_last": snap.hashrate_last,
        "hashrate_human": format_rate(snap.hashrate_ema),
        "workers": snap.workers,
        "pool_url": snap.pool_url,
        "current_pool": snap.pool_url,
        "pool_difficulty": snap.pool_difficulty,
        "current_job_id": snap.current_job_id,
        "shares_total": snap.shares_total,
        "shares_accepted": snap.shares_accepted,
        "shares_rejected": snap.shares_rejected,
        "last_share_ts": snap.last_share_ts,
        "uptime_s": snap.uptime_s,
        "uptime_human": format_uptime(snap.uptime_s),
        "sha_backend": provider.sha_backend,
        "started_at": snap.started_at,
        "now": time.time(),
    }


# ─────────────────── HTML ───────────────────

_HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>hope-hash dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    color-scheme: dark;
    --bg: #0d1117;
    --panel: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --muted: #7d8590;
    --accent: #58a6ff;
    --ok: #3fb950;
    --bad: #f85149;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: var(--bg); color: var(--text);
  }
  header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 24px; }
  h1 { font-size: 1.4rem; margin: 0; }
  .tag { color: var(--muted); font-size: 0.9rem; }
  .grid {
    display: grid; gap: 16px;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  }
  .card {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; padding: 16px;
  }
  .card .label { color: var(--muted); font-size: 0.8rem; text-transform: uppercase;
    letter-spacing: 0.05em; margin-bottom: 8px; }
  .card .value { font-size: 1.6rem; font-variant-numeric: tabular-nums; word-break: break-all; }
  .hero { grid-column: 1 / -1; }
  .hero .value { font-size: 2.6rem; }
  svg.spark { width: 100%; height: 60px; margin-top: 8px; }
  svg.spark path { fill: none; stroke: var(--accent); stroke-width: 1.5; }
  .events {
    margin-top: 24px; background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; padding: 16px; max-height: 320px; overflow-y: auto;
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
    font-size: 0.85rem;
  }
  .events h2 { margin: 0 0 8px; font-size: 1rem; }
  .ev { padding: 4px 0; border-bottom: 1px dashed var(--border); }
  .ev:last-child { border-bottom: none; }
  .ev .ts { color: var(--muted); margin-right: 8px; }
  .ev.share_accepted .type { color: var(--ok); }
  .ev.share_rejected .type { color: var(--bad); }
  .ev .type { display: inline-block; min-width: 110px; }
  footer { margin-top: 24px; color: var(--muted); font-size: 0.8rem; }
  a { color: var(--accent); }
</style>
</head>
<body>
<header>
  <h1>hope-hash</h1>
  <span class="tag">solo BTC miner dashboard</span>
</header>

<section class="grid">
  <div class="card hero">
    <div class="label">hashrate (EMA)</div>
    <div class="value" id="hashrate">—</div>
    <svg class="spark" id="spark" viewBox="0 0 600 60" preserveAspectRatio="none"></svg>
  </div>
  <div class="card"><div class="label">pool</div><div class="value" id="pool">—</div></div>
  <div class="card"><div class="label">sha backend</div><div class="value" id="backend">—</div></div>
  <div class="card"><div class="label">uptime</div><div class="value" id="uptime">—</div></div>
  <div class="card"><div class="label">workers</div><div class="value" id="workers">—</div></div>
  <div class="card"><div class="label">job id</div><div class="value" id="job">—</div></div>
  <div class="card"><div class="label">pool diff</div><div class="value" id="diff">—</div></div>
  <div class="card"><div class="label">shares (sent)</div><div class="value" id="sent">—</div></div>
  <div class="card"><div class="label">shares accepted</div><div class="value" id="ok">—</div></div>
  <div class="card"><div class="label">shares rejected</div><div class="value" id="rej">—</div></div>
  <div class="card"><div class="label">last share</div><div class="value" id="last">—</div></div>
</section>

<section class="events">
  <h2>recent events</h2>
  <div id="evlist"></div>
</section>

<footer>
  polls <code>/api/stats</code> every 2s · streams <code>/api/events</code> via SSE ·
  <a href="/healthz">/healthz</a>
</footer>

<script>
  // Кольцевой буфер последних 60 значений хешрейта для sparkline.
  const HIST = 60;
  const samples = [];

  function fmtAgo(ts) {
    if (ts == null) return "never";
    const d = Math.max(0, Date.now() / 1000 - ts);
    if (d < 60) return d.toFixed(0) + "s ago";
    if (d < 3600) return (d / 60).toFixed(1) + "m ago";
    return (d / 3600).toFixed(1) + "h ago";
  }

  function trunc(s, n) {
    if (s == null) return "—";
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  function setText(id, v) { document.getElementById(id).textContent = v; }

  function drawSpark() {
    const svg = document.getElementById("spark");
    if (!samples.length) { svg.innerHTML = ""; return; }
    const w = 600, h = 60, pad = 4;
    const maxv = Math.max.apply(null, samples) || 1;
    const minv = Math.min.apply(null, samples);
    const range = Math.max(1, maxv - minv);
    const dx = (w - 2 * pad) / Math.max(1, samples.length - 1);
    const pts = samples.map((v, i) => {
      const x = pad + i * dx;
      const y = h - pad - ((v - minv) / range) * (h - 2 * pad);
      return (i === 0 ? "M" : "L") + x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    svg.innerHTML = '<path d="' + pts + '"/>';
  }

  async function refresh() {
    try {
      const r = await fetch("/api/stats", { cache: "no-store" });
      if (!r.ok) return;
      const s = await r.json();
      setText("hashrate", s.hashrate_human);
      setText("pool", s.pool_url || "(disconnected)");
      setText("backend", s.sha_backend);
      setText("uptime", s.uptime_human);
      setText("workers", s.workers);
      setText("job", trunc(s.current_job_id, 24));
      setText("diff", s.pool_difficulty);
      setText("sent", s.shares_total);
      setText("ok", s.shares_accepted);
      setText("rej", s.shares_rejected);
      setText("last", fmtAgo(s.last_share_ts));
      samples.push(s.hashrate_ema);
      while (samples.length > HIST) samples.shift();
      drawSpark();
    } catch (e) { /* network blip — try again on next tick */ }
  }

  function appendEvent(type, payload) {
    const list = document.getElementById("evlist");
    const div = document.createElement("div");
    div.className = "ev " + type;
    const ts = new Date().toISOString().slice(11, 19);
    div.innerHTML = '<span class="ts">' + ts + '</span>' +
                    '<span class="type">' + type + '</span>' +
                    '<span class="payload"></span>';
    div.querySelector(".payload").textContent = JSON.stringify(payload);
    list.insertBefore(div, list.firstChild);
    while (list.childNodes.length > 50) list.removeChild(list.lastChild);
  }

  function startSSE() {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource("/api/events");
    ["share_found", "share_accepted", "share_rejected", "job", "pool"].forEach(t => {
      es.addEventListener(t, ev => {
        try { appendEvent(t, JSON.parse(ev.data)); } catch (e) { appendEvent(t, {}); }
      });
    });
    es.onerror = () => { /* browser will auto-reconnect */ };
  }

  refresh();
  setInterval(refresh, 2000);
  startSSE();
</script>
</body>
</html>
"""


# ─────────────────── HTTP handler ───────────────────


def _make_handler(
    provider: StatsProvider,
    health_provider_ref: list[Optional[HealthProvider]],
) -> type[BaseHTTPRequestHandler]:
    """Фабрика handler-класса с замыканиями на provider/health.

    Тот же приём, что в ``metrics._make_handler``: всё через замыкания,
    health-провайдер — однослотовый mutable container, чтобы можно было
    подменить после ``start()``.
    """

    class _Handler(BaseHTTPRequestHandler):
        # Server-Sent Events требует HTTP/1.1 для chunked transfer.
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 — имя задано базовым классом
            if self.path == "/" or self.path == "/index.html":
                self._serve_html()
                return
            if self.path == "/api/stats":
                self._serve_stats()
                return
            if self.path == "/api/events":
                self._serve_events()
                return
            if self.path == "/healthz":
                self._serve_healthz(health_provider_ref[0])
                return
            self.send_error(404)

        # ─── individual endpoints ───

        def _serve_html(self) -> None:
            body = _HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_stats(self) -> None:
            payload = _stats_payload(provider)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_healthz(self, hp: Optional[HealthProvider]) -> None:
            if hp is None:
                payload: dict[str, Any] = {
                    "status": "down",
                    "reason": "no health provider registered",
                }
                http_status = 503
            else:
                try:
                    payload = hp() or {}
                except Exception as exc:  # noqa: BLE001 — пользовательский callable
                    payload = {"status": "down", "reason": f"provider error: {exc}"}
                    http_status = 503
                else:
                    status = payload.get("status", "down")
                    http_status = 503 if status == "down" else 200
            body = json.dumps(payload).encode("utf-8")
            self.send_response(http_status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_events(self) -> None:
            """SSE-стрим: подписываемся на provider, гоняем события в сокет.

            Завершается, когда:
            - клиент закрыл коннект (BrokenPipeError/ConnectionResetError);
            - сервер останавливается (provider.stop_event эквивалент:
              мы держим короткие таймауты и проверяем connection alive).
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            # Полностью отключаем nginx/прокси-буферизацию, иначе события
            # копятся пока не наберётся 4кб и UI «зависает».
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            ev_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(
                maxsize=_SSE_QUEUE_MAX
            )

            def _on_event(event_type: str, payload: dict[str, Any]) -> None:
                # Никогда не блокируем producer'а: если очередь полная,
                # дропаем событие и логируем (лучше потерять одно
                # событие, чем повесить mine-thread).
                try:
                    ev_queue.put_nowait((event_type, payload))
                except queue.Full:
                    logger.warning("[webui] SSE queue full, dropping event %s", event_type)

            unsubscribe = provider.subscribe(_on_event)
            try:
                last_keepalive = time.time()
                while True:
                    try:
                        event_type, payload = ev_queue.get(timeout=1.0)
                    except queue.Empty:
                        # Нет событий — возможно, время для keepalive.
                        if time.time() - last_keepalive >= _SSE_KEEPALIVE_S:
                            try:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                break
                            last_keepalive = time.time()
                        continue
                    try:
                        data = json.dumps(payload, default=str)
                        msg = f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")
                        self.wfile.write(msg)
                        self.wfile.flush()
                        last_keepalive = time.time()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        # Клиент ушёл — выходим без шумного traceback.
                        break
            finally:
                unsubscribe()

        # ─── housekeeping ───

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            # Тот же приём, что в metrics: единый канал — logger "hope_hash",
            # засорять stderr каждым GET / не нужно.
            return

    return _Handler


# ─────────────────── server lifecycle ───────────────────


class WebUIServer:
    """HTTP-дашборд на отдельной нити. Старт/стоп идемпотентны.

    Использование::

        provider = StatsProvider(...)
        server = WebUIServer(provider, port=8080)
        server.start()
        # ... майнер работает ...
        server.stop()
    """

    def __init__(
        self,
        provider: StatsProvider,
        host: str = "127.0.0.1",
        port: int = 8080,
    ) -> None:
        self.provider = provider
        self.host = host
        self.port = int(port)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._health_ref: list[Optional[HealthProvider]] = [None]

    def set_health_provider(self, hp: Optional[HealthProvider]) -> None:
        """Регистрирует health-провайдера (тот же контракт, что в ``MetricsServer``)."""
        self._health_ref[0] = hp

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._server is not None:
                return
            handler_cls = _make_handler(self.provider, self._health_ref)
            self._server = ThreadingHTTPServer((self.host, self.port), handler_cls)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name=f"hope_hash-webui-{self.port}",
                daemon=True,
            )
            self._thread.start()
            logger.info("[webui] дашборд на %s", self.url)

    def stop(self, timeout: float = 2.0) -> None:
        with self._lifecycle_lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=timeout)
        if server is not None:
            logger.info("[webui] дашборд остановлен")

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"


# Безопасный alias на случай, если кто-то ожидает функцию-фабрику HTML
# (например, для предзаполнения страницы в шаблоне). HTML — статика, но
# делаем доступным как функцию для тестов и переиспользования.
def render_html() -> str:
    """Возвращает HTML-страницу дашборда (статика, без подстановок)."""
    return _HTML_PAGE


# Экранируем на всякий случай, если когда-нибудь добавим динамическую
# подстановку. Сейчас не используется, но импортируется в тестах.
def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)
