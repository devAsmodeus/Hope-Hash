# ROADMAP

План развития проекта. Разбит на четыре уровня по сложности, плюс отдельный блок «идеи поверх скелета». Можно идти по уровням, можно выдёргивать пункты выборочно.

---

## Уровень 0 — стабилизация ✅ ЗАВЕРШЁН (2026-04-30)

Базовая надёжность. Закрыт целиком одним пассом + реструктуризация в `src/`-layout.

- [x] **Reconnect-логика.** `supervisor_loop` с backoff 1→2→4→…→60с, повторный subscribe/authorize, сброс `current_job`.
- [x] **Обработка `mining.set_extranonce`.** В `_handle_message` — обновляем `extranonce1`/`extranonce2_size` под локом, инвалидируем job.
- [x] **Корректное завершение.** Общий `stop_event` для всех нитей, `client.close()` разблокирует `recv()`, `reader_thread.join(timeout=5)`. Никаких висящих daemon-нитей.
- [x] **Логирование вместо `print`.** `logger = getLogger("hope_hash")`, формат `%(asctime)s [%(levelname)s] %(message)s`, уровни INFO/WARNING. Тэги `[net]/[stratum]/[mine]/[stats]/[main]` сохранены внутри сообщений.
- [ ] **Конфиг через CLI и/или YAML.** Перенесено в Уровень 1 (UI/UX) — runtime-аргументы уже через argparse, YAML добавим позже при необходимости.
- [x] **Юнит-тесты.** 15 тестов в `tests/test_block.py` на `double_sha256`, `swap_words`, `difficulty_to_target`, `build_merkle_root`. Векторы из mainnet.

**Бонусом сделано в этом же раунде:**
- [x] Реструктуризация в `src/`-layout (пакет `hope_hash`).
- [x] `pyproject.toml` с `hatchling` backend, dist-name `hope-hash`.
- [x] Console script `hope-hash` + `python -m hope_hash`.
- [x] CI matrix (`.github/workflows/ci.yml`): Python 3.11/3.12/3.13 × ubuntu/windows/macos.
- [x] LICENSE (MIT), CHANGELOG.md, .gitignore, .editorconfig, Makefile.

---

## Уровень 1 — лёгкие апгрейды (вечер каждый)

**Статус (2026-04-30):** производительность + наблюдаемость — закрыто.
TUI и команды Telegram — отложены.

Видимые фичи, не требующие глубокой переработки.

### Производительность

- [x] **Multiprocessing.** `parallel.py`, CLI `--workers N`. nonce-пространство `[0, 2³²)` делится поровну между N процессами. found_queue + hashes_counter (`mp.Value('Q')`). На 16-CPU машине default = 15 воркеров.
- [x] **EMA-хешрейт.** alpha=0.3, окно 5с. Сэмпл = дельта счётчика / dt. Логируется и в `[stats]`, и в Prometheus gauge `hopehash_hashrate_hps`.

### UI/UX

- [ ] **TUI на `rich`.** Не делаем — `rich` не входит в stdlib. Заменено на `curses` ниже.
- [x] **`curses` дашборд** (`--tui`, v0.5.0). На Windows degrade без падения.
- [x] **ASCII-арт логотип** при старте (`banner.py`, v0.5.0). Гасится `--no-banner`.

### Telegram-бот

- [x] **Исходящие уведомления.** `notifier.py` через stdlib `urllib`, без `python-telegram-bot`. События: started / stopped / share_accepted / block_found / disconnected / reconnected. Конфиг через env (`HOPE_HASH_TELEGRAM_TOKEN`, `HOPE_HASH_TELEGRAM_CHAT_ID`). Если переменные не заданы — модуль молча disabled.
- [x] **Входящие команды `/stats`, `/restart`, `/stop`** (long polling, v0.5.0). Authz по chat_id, opt-in через `HOPE_HASH_TELEGRAM_INBOUND=1`.

### Логи и метрики

- [x] **SQLite-журнал** (`storage.py`). Таблицы `shares` (ts, job_id, nonce_hex, hash_hex, difficulty, accepted, is_block) и `sessions`. WAL-режим, потокобезопасность через `threading.Lock`.
- [x] **Prometheus-экспортёр** (`metrics.py`). Эндпоинт `/metrics` на `http.server` (`ThreadingHTTPServer` в фоновой нити). Метрики: `hopehash_shares_total`, `hopehash_hashrate_hps`, `hopehash_pool_difficulty`, `hopehash_workers`, `hopehash_uptime_seconds`. CLI `--metrics-port` (0 — выключить).
- [x] **Grafana-дашборд** (`deploy/grafana/hope-hash.json`, v0.5.0). 5 панелей: hashrate / pool diff / shares stacked / workers / uptime.

---

## Уровень 2 — серьёзные фичи (несколько дней)

### Производительность

- [x] **ctypes-обёртка над libcrypto SHA-256.** `sha_native.py` (v0.6.0): грузит `libcrypto-3.dll` / `libcrypto.so.3` / `/usr/lib/libcrypto.dylib` через `ctypes.CDLL`, EVP API. CLI `--sha-backend {auto,hashlib,ctypes}`. Без mid-state — для бенчмарка-сравнения. Hot path майнинга остался на hashlib mid-state, как самый быстрый вариант на pure-stdlib.
- [ ] **SIMD/C-extension для SHA-256.** Дальнейшее ускорение требует SIMD (AVX2: 8 хешей параллельно). Это уже C-расширение, не stdlib — Уровень 3.
- [ ] **SIMD-реализация SHA-256.** AVX2 (8 хешей параллельно) или AVX-512 (16). Можно взять готовое из репо `intel-ipsec-mb` или `sha-2-multihash`. Пишется как C-extension, дёргается из Python.
- [x] **Mid-state кэширование.** `hashlib.sha256().copy()` после первых 64 байт — константа в рамках nonce-цикла. Реализовано в `parallel.worker` (v0.3.0). Прирост ≈×1.5–2, zero deps.

### Архитектура

- [x] **Множественные пулы с failover.** `--pool host:port` повторяемый (v0.6.0). После N (default 3) подряд провалов на одном пуле supervisor ротирует на следующий, после полного круга применяется exponential backoff. `pools.PoolList` + `StratumClient.set_endpoint()`.
- [ ] **Несколько воркеров на разных пулах одновременно.** Распределённая работа с разными адресами/именами.
- [x] **Поддержка vardiff.** `mining.suggest_difficulty` после авторизации. CLI-флаг `--suggest-diff FLOAT`. Реализовано в `stratum.py` (v0.3.0).

### Web-морда

- [x] **Web-дашборд на stdlib `http.server`** (`webui.py`, v0.7.0):
  CLI `--web-port`, single-page HTML без CDN, vanilla JS, inline-SVG
  sparkline. Эндпоинты:
  - `GET /` — HTML-дашборд с polling `/api/stats` каждые 2с.
  - `GET /api/stats` — JSON snapshot (no-store).
  - `GET /api/events` — Server-Sent Events (`share_*` / `job` / `pool`).
  - `GET /healthz` — то же тело, что у metrics-сервера.
  FastAPI / WebSocket вариант не делаем — Stdlib + SSE покрывает
  потребности дашборда без новых зависимостей.
- [ ] **POST /restart / POST /stop через web** — остался на потом
  (Telegram-команды уже есть). Веб-эндпоинты потребуют CSRF + auth.
- [ ] **Конфиг через web-интерфейс**, чтобы не редактировать YAML руками.

---

## Уровень 3 — для упоротых (недели)

### Радикальная производительность

- [ ] **Rust core через PyO3.** Переписать `mine()` на Rust с `sha2` крейтом и SIMD-оптимизациями. Дёргать из Python как обычный модуль. Ожидаемый прирост: с ~100 KH/s до ~10 MH/s.
- [ ] **GPU через PyOpenCL или CUDA через `cupy`.** На RTX 4090 — порядка 2 ГН/с. Это всё ещё в 100 000 раз меньше одного ASIC, но уже не лотерея масштаба «10¹³ дней», а «10⁸ дней».
- [ ] **FPGA-сборка.** Если есть Xilinx/Lattice плата — синтез SHA-256 ядра, общение через UART/USB.

### Протоколы

- [ ] **Stratum V2.** Современный бинарный протокол с шифрованием (Noise) и job negotiation. У `solo.ckpool.org` его пока нет, но есть на других пулах. Хорошая возможность разобраться в современном крипто-протоколе.
- [x] **Прямое подключение к bitcoin-core.** `solo.py` (v0.6.0): `--solo --rpc-url URL --rpc-cookie PATH`. Polling `getblocktemplate` каждые `--solo-poll-sec`, сборка coinbase (BIP-34 + BIP-141 witness commitment), `submitblock` через JSON-RPC. Учебное качество, шанс найти блок ≈ 0; цель — научить, как `getblocktemplate` устроен.

### Мониторинг и SRE

- [x] **Healthchecks endpoint.** `/healthz` JSON на metrics-сервере (v0.5.0). 200/503, флаг `--healthz-stale-after`.
- [x] **Docker-образ.** `Dockerfile` (`python:3.11-slim`, healthcheck через stdlib `urllib`) + `docker-compose.yml` (miner + Prometheus + Grafana, volumes для SQLite и provisioning) + `.dockerignore` (v0.7.0).
- [ ] **Helm chart**, если совсем хочется хардкора с k8s на одном Raspberry Pi.

---

## Идеи поверх скелета (для веселья)

Не путь развития, а отдельные мини-проекты, которые можно реализовать поверх кода.

- [x] **Demo-режим без подключения к пулу.** `hope-hash --demo [--demo-diff DIFF]`. Синтетический заголовок, low-diff target, multiprocessing-воркеры. Реализовано в `demo.py` (v0.3.0).
- [ ] **«Гуманизированная» статистика.** «При твоём хешрейте средний шанс найти блок: раз в 47 миллиардов лет». Считается из текущего сетевого difficulty (получаем через bitcoin-core RPC или публичные API типа `mempool.space`).
- [ ] **Lottery-визуализация.** Каждый хеш — точка на canvas. Цвет = первые 3 байта хеша. Просто красивая бесконечная анимация. Можно собрать на `pygame` или в браузере через WebSocket.
- [x] **Бенчмарк-режим (pure-Python).** `hope-hash --benchmark [--bench-duration SEC]`. Меряет хешрейт на синтетическом заголовке с target=0, репортит платформу/CPU/python и H/s. Реализовано в `bench.py` (v0.4.0). Baseline на Intel i7-12700H: ~570 KH/s/worker. Multi-backend сравнение (ctypes/C-extension/Rust/OpenCL) — отдельная задача после Уровня 2.
- [ ] **Майнинг shitcoin-ов на той же базе.** Litecoin использует Scrypt, Dogecoin — тоже Scrypt. Если заменить хеш-функцию и пул, можно майнить с шансом найти блок раз в «всего» миллион лет вместо миллиарда. Кардинально меняется сложность кода.
- [ ] **«Майнер на ладошке».** Запаковать в `pyinstaller` или `nuitka` в один бинарник с TUI — чтобы можно было кому-то отдать `.exe` и они «майнили» (с нулевым шансом, но прикольно).
- [ ] **Лидерборд между друзьями.** Несколько твоих знакомых ставят майнер с разными worker-name на один и тот же BTC-адрес. Лидерборд показывает, кто из вас вносит больше хешрейта в общий пул. Если кто-то найдёт блок (хаха) — делите по вкладу.

---

## Сознательно отложено (не включено в v0.7.0)

После трёх PR'ов (ops/UX, perf/resilience, web/docs) вот что **намеренно**
не сделано — каждое требует либо новой зависимости, либо отдельного
крупного эпика:

- **Stratum V2.** Бинарный протокол с Noise-handshake. Реализация
  Noise на stdlib возможна, но нетривиальный sub-project; ждёт
  отдельного PR.
- **Rust core через PyO3.** Требует Rust toolchain в build. Ожидаемый
  выигрыш ~×100, но это уже не «учебный проект, который можно
  установить через `pip install -e .`».
- **GPU (PyOpenCL / cupy).** Большие сторонние зависимости и
  привязка к драйверам. Не вписывается в pure-stdlib.
- **FastAPI вместо stdlib `http.server`.** Дала бы swagger / async, но
  это +большая зависимость. Текущий webui выдерживает все требования
  дашборда без них.
- **Helm chart / k8s manifests.** Compose-стек покрывает все реальные
  use-кейсы для проекта такого масштаба.
- **POST `/stop` / `/restart` через web.** Telegram-команды уже
  существуют; web-write-эндпоинты потребуют auth + CSRF — отдельный
  эпик.

## Если выбирать одно

Если энергии хватит только на один шаг после стабилизации (Уровень 0), то самое осмысленное:

**TUI на `rich` + multiprocessing + Telegram-бот.**

Это даст:
- видимую разницу (красивый дашборд),
- ощутимый прирост хешрейта (×4–8 на современном ноуте),
- эмоциональную связь с проектом (телеграм пингает «принят шар!» прямо в карман).

После этой связки уже понятно, хочется ли копать в производительность (Rust/SIMD) или в фичи (web-морда, мульти-пулы) — и можно идти по соответствующей ветке.
