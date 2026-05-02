# PR C summary — `feat/web-and-docs` (v0.7.0)

Web-дашборд (stdlib `http.server` + SSE), Docker-стек, провижининг
Prometheus/Grafana, двуязычная документация, README rewrite. Pure stdlib,
ноль новых рантайм-зависимостей. Тесты: **225 → 242** (+17).

## File map

### Added

| Path | Purpose |
| --- | --- |
| `src/hope_hash/webui.py` | `WebUIServer` (stdlib `http.server`), `render_html()`, обработчики `/`, `/api/stats`, `/api/events` (SSE), `/healthz`. Daemon-нить, идемпотентные start/stop. |
| `tests/test_webui.py` | 17 тестов: pub/sub `StatsProvider`, render_html, HTML/JSON-эндпоинты, SSE-стрим с реальным сокетом, healthz fallback, lifecycle. |
| `Dockerfile` | `python:3.11-slim`, `pip install -e .` самого проекта, healthcheck через stdlib `urllib`, `ENTRYPOINT ["hope-hash"]`. |
| `docker-compose.yml` | Три сервиса (`miner` + `prometheus` + `grafana`) с volumes (`./data`, named `prom-data`/`grafana-data`), env vars документированы в комментариях. |
| `.dockerignore` | Исключает `.git`, `tests`, `docs`, `*.db`, `__pycache__`, IDE-файлы, чтобы build-context был минимальным. |
| `deploy/prometheus/prometheus.yml` | Минимальный scrape config: `miner:8000/metrics` каждые 15с. |
| `deploy/grafana/datasource.yml` | Provisioning Prometheus-датасорса (`url: http://prometheus:9090`). |
| `deploy/grafana/dashboard.yml` | Provisioning дашбордов из `/var/lib/grafana/dashboards` → подхватывает `hope-hash.json` из PR A. |
| `docs/getting-started.en.md` | Первый запуск для пользователя без bitcoin-опыта (Python install → BTC адрес → `hope-hash` → чтение логов → типичные пробемы). |
| `docs/getting-started.ru.md` | То же, по-русски. Не machine-translated, переписано естественно. |
| `docs/deploy.en.md` | Compose walkthrough, Telegram setup, healthcheck, reverse-proxy snippet. |
| `docs/deploy.ru.md` | Русская версия, тот же набор разделов. |
| `docs/architecture.en.md` | Протокол, threading-таблица, file map, hot path, observers, ctypes trade-off, solo caveats, BIP-ссылки. |
| `docs/architecture.ru.md` | Русская версия, mirror. |
| `docs/handoff/pr-c-summary.md` | Этот файл. |

### Modified

| Path | Why |
| --- | --- |
| `src/hope_hash/tui.py` | `StatsProvider` теперь принимает `sha_backend`, имеет `subscribe()/publish_event()/_publish()`, `update_job/record_share/update_pool` пушат события. Добавлен property `sha_backend` и `set_sha_backend()`. |
| `src/hope_hash/cli.py` | Новые флаги `--web-port` и `--web-host`. `_health_provider` вынесен наружу из `if metrics_server` — теперь тот же health-провайдер шарится между metrics и webui. WebUIServer стартует и стопится в lifecycle. `StatsProvider` инициализируется с `sha_backend`. |
| `src/hope_hash/__init__.py` | Bump `__version__` → `0.7.0`. Re-export `WebUIServer`, `render_html`. |
| `README.md` | Полный rewrite: EN-half сверху, RU-half снизу, оба зеркалят одинаковые разделы. Cross-link на `docs/`. |
| `CHANGELOG.md` | Секция `[0.7.0]` с детальным списком добавлений и изменений. |
| `ROADMAP.md` | Тикнуты web-морда (через stdlib, не FastAPI) и Docker. Добавлен раздел «Сознательно отложено» (Stratum V2, Rust/PyO3, GPU, FastAPI, Helm). |

## New CLI flags

| Flag | Default | Notes |
| --- | --- | --- |
| `--web-port PORT` | `0` (off) | Web-дашборд (HTML + `/api/stats` + SSE `/api/events` + `/healthz`). |
| `--web-host HOST` | `127.0.0.1` | Bind-хост. По умолчанию loopback — наружу только за reverse-proxy. |

## New endpoints

| Endpoint | Content | Notes |
| --- | --- | --- |
| `GET /` | text/html | Single-page dashboard, vanilla JS, inline-SVG sparkline, polls `/api/stats` каждые 2с. |
| `GET /api/stats` | application/json | Snapshot: hashrate, pool, sha_backend, current_job_id, shares_*, uptime. `Cache-Control: no-store`. |
| `GET /api/events` | text/event-stream | SSE: `share_found`, `share_accepted`, `share_rejected`, `job`, `pool`. Keep-alive каждые 15с. |
| `GET /healthz` | application/json | Те же тело и коды, что у `MetricsServer.set_health_provider`. |

## New env vars

Никаких новых обязательных env vars. Compose читает существующие
(`HOPE_HASH_TELEGRAM_TOKEN/CHAT_ID/INBOUND`, `BTC_ADDRESS`, `WORKERS`,
`GRAFANA_USER/PASSWORD`).

## Architecture additions

- `StatsProvider.subscribe(callback) -> unsubscribe()` — pub/sub шина для
  SSE. Колбэк вызывается синхронно из публикующей нити, поэтому
  обработчики обязаны быть моментальными (в webui это `queue.put_nowait`).
  Сломанный подписчик не валит publish: исключения ловятся и логируются.
- `StatsProvider.publish_event(event_type, payload)` — публичный alias
  на `_publish` для прямого вызова из miner кода (на будущее).
- `update_job` публикует `job`-event только при реальной смене job_id —
  это защищает SSE от шторма событий на каждом пересчёте хешрейта.
- `WebUIServer` зеркалит API `MetricsServer`: `start()`, `stop()`,
  `set_health_provider()`. Health-провайдер — однослотовый mutable
  container (тот же приём, что в `metrics._make_handler`).

## Gotchas

1. **SSE нужен HTTP/1.1.** В webui handler выставляет
   `protocol_version = "HTTP/1.1"`. Если кто-то унаследует handler и
   откатит на 1.0 — chunked transfer перестанет работать.
2. **`X-Accel-Buffering: no`** обязателен для nginx, иначе SSE копится
   в буфере ответа. Уже выставлен.
3. **Порт 8000 vs 9090.** В compose-стеке metrics+healthz на 8000,
   webui на 8001 — то, что Prometheus и Grafana ожидают увидеть.
   Healthcheck Dockerfile тоже бьёт в 8000. Если меняешь — синхронизируй.
4. **Web-host default `127.0.0.1`.** В compose явно ставим
   `--web-host 0.0.0.0`, иначе порт-маппинг не достанется до контейнера.
5. **Healthcheck без `curl`.** Slim-образ `python:3.11-slim` не имеет
   `curl`, и ставить его ради healthcheck — лишние ~10MB. Используем
   stdlib `urllib`. Команда написана как `python -c "..."`, переносов
   строк не имеет.
6. **`StatsProvider` обратной совместимости.** Старый аргумент
   `pool_url` остался; новый `sha_backend` — keyword. Все ныне
   существующие вызовы `StatsProvider(pool_url=...)` работают без
   изменений.
7. **Подписчики держатся вечно.** Если веб-клиент отвалился, мы
   снимаем его подписку в `finally`. Но если callback пользователя
   удерживает ссылку — leak. В нашем коде только webui подписывается,
   и он всегда `unsubscribe()` в `finally`.

## Open questions for future work

- **POST /restart / POST /stop через web.** Telegram уже умеет; для web
  потребуется минимум CSRF-токен + Basic-auth. Не делаем без явной
  просьбы.
- **WebSocket вместо SSE.** SSE простой, односторонний, чисто работает
  через прокси. WebSocket даст двунаправленность, но потребует stdlib
  `wsproto`-equivalent (нет встроенного). Не делаем.
- **Authz для `/api/stats`.** Снапшот не содержит секретов (адрес виден
  в логах compose, токены — в env), но при выставлении наружу
  reverse-proxy с Basic-auth обязателен. Документация в `deploy.{en,ru}.md`.
- **CSP-заголовок для `/`.** HTML inline JS — нужно `script-src
  'self' 'unsafe-inline'`. Сейчас не выставляется. Для интранет-деплоя
  не критично; для публичного — добавить.
- **Метрика `webui_active_streams`.** Чтобы видеть, сколько
  SSE-клиентов подключено. Сейчас не считается.

## Verification

```bash
py -3.11 -m unittest discover -s tests -v
# Ran 242 tests in ~19s — OK

py -3.11 -m hope_hash --help
py -3.11 -m hope_hash --benchmark --bench-duration 1 --workers 1
py -3.11 -m hope_hash --demo --workers 2

# Smoke-тест web-дашборда (требует валидный BTC-адрес и Internet к solo.ckpool.org):
# py -3.11 -m hope_hash bc1q...your_address... mylaptop --web-port 8001 &
# curl -s http://127.0.0.1:8001/api/stats | jq .
# curl -N http://127.0.0.1:8001/api/events     # SSE
# open http://127.0.0.1:8001/                  # HTML

# Docker (опционально):
# docker build -t hope-hash:0.7.0 .
# BTC_ADDRESS=bc1q... docker compose up -d
# open http://localhost:8001
```

No `pip install` of third-party deps. No changes to `block.py` endianness
или `parallel._worker_hashlib_midstate` hot path. Существующие 225 тестов
зелёные, новые 17 покрывают webui.

## Review checklist (для review-панели)

- [ ] `webui.py` — нет ли утечек `subscribe()` без `unsubscribe()` (только в
      `_serve_events`, защищено `try/finally`).
- [ ] `webui.py` — `_serve_events` обрабатывает `BrokenPipeError`,
      `ConnectionResetError`, `OSError` при `wfile.write`/`flush`.
- [ ] `webui.py` — `_SSE_QUEUE_MAX` (256) и keepalive 15с — разумные
      defaults; флуд событий не валит mine-thread (`put_nowait` →
      drop с warning, не блок).
- [ ] `webui.py` HTML — нет `<script src=>`, нет внешних URL, нет
      шрифтов/CDN; всё inline, работает без сети.
- [ ] `tui.py` — `_publish` снимает список подписчиков под локом, потом
      вызывает без удержания лока (избегает deadlock'ов при
      `unsubscribe` внутри callback).
- [ ] `cli.py` — `WebUIServer.stop()` вызывается в `finally` до
      `metrics_server.stop()`, чтобы SSE-клиенты успели разорваться
      без ошибок в логах.
- [ ] `Dockerfile` — `pip install -e .` ставит сам проект, не сторонние
      зависимости; healthcheck использует stdlib (без `curl`).
- [ ] `docker-compose.yml` — `BTC_ADDRESS:?` фейлится с понятным
      сообщением, если не задан; `HOPE_HASH_TELEGRAM_*` опциональны.
- [ ] `deploy/prometheus/prometheus.yml` — путь до миничёра
      `miner:8000` (имя сервиса в compose).
- [ ] `deploy/grafana/dashboard.yml` — `path` совпадает с тем, куда
      compose монтирует `./deploy/grafana`.
- [ ] README — EN-half и RU-half имеют одинаковые разделы в одинаковом
      порядке (что / install / run / advanced flags / demo / benchmark /
      architecture / realistic expectations / contributing).
- [ ] `docs/*.{en,ru}.md` — линки между языками рабочие; код-блоки
      copy-pasteable.
- [ ] `__version__` = `"0.7.0"` в `__init__.py`; pyproject.toml читает
      version через `[tool.hatch.version] path = "src/hope_hash/__init__.py"`.
- [ ] CHANGELOG `[0.7.0]` секция перед `[0.6.0]`, описание матчит
      реальный diff.
- [ ] ROADMAP — тикнуты web-морда и Docker; «Сознательно отложено»
      честно отражает скоп PR'ов A/B/C.
- [ ] Нет третьих зависимостей под `src/hope_hash/`.
- [ ] Hot path (`parallel.py`, `block.py`) не тронут.
- [ ] Endianness и word-swap в `miner._build_header_base` не тронуты.
