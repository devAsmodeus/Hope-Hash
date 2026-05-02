# Changelog

Все значимые изменения проекта отражены здесь.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект придерживается [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

## [0.6.0] — 2026-05-02

### Добавлено
- **Multi-pool failover** (`pools.py`): `--pool host:port` повторяемый.
  При N (default 3) подряд провалах на одном пуле supervisor ротирует
  на следующий. После полного круга без успехов применяется обычный
  exponential backoff. `PoolList.full_cycle_failed()` отдаёт сигнал.
  `StratumClient.set_endpoint(host, port)` — без пересоздания клиента,
  локи и `on_share_result` сохраняются.
- **Solo-режим через `getblocktemplate`** (`solo.py`): `hope-hash --solo
  --rpc-url URL --rpc-cookie PATH` (или `--rpc-user/--rpc-pass`).
  `SoloClient` имитирует поверхность `StratumClient`, чтобы `mine()`
  работал без изменений. Polling `getblocktemplate` каждые
  `--solo-poll-sec` (5с по умолчанию). На find — собирает coinbase
  (BIP-34 height + extranonce), считает witness commitment (BIP-141)
  если `default_witness_commitment` присутствует, сериализует полный
  блок и шлёт `submitblock`. JSON-RPC через stdlib `urllib`.
- **ctypes SHA-256 backend** (`sha_native.py`): `--sha-backend
  {auto,hashlib,ctypes}` (default `auto`). Загружает `libcrypto-3.dll`
  / `libcrypto.so.3` / `/usr/lib/libcrypto.dylib` через `ctypes.CDLL`,
  вызывает EVP API. Без mid-state — для честного бенчмарка. Если
  libcrypto не нашёлся, тихо fallback на `hashlib`.
- **`--benchmark --backends`** (`bench.py`): прогоняет один и тот же
  бенч по всем доступным backend'ам и печатает сравнение. Финальная
  строка вида `[bench] result: ctypes 1.42 MH/s (1.85x vs hashlib-midstate)`.
- **Тесты**: `test_pools.py` (24), `test_sha_native.py` (12),
  `test_solo.py` (35, включая `FakeRPC`), `test_bench_backends.py` (9).
  Итого 145 → 225 (+80).

### Изменено
- `supervisor_loop()` принимает опциональные `pools: PoolList` и
  `stats_provider: StatsProvider`. Без них поведение прежнее.
- `mine()` принимает `sha_backend: str` (default `"hashlib"`).
- `start_pool()` пробрасывает `sha_backend` в каждый воркер.
- `worker()` (parallel.py) рефакторен: `_worker_hashlib_midstate()`
  (hot path, без изменений) и `_worker_ctypes()` (sha256d на каждой
  итерации без mid-state).
- `StatsProvider.update_pool(url)` — для отображения активного пула
  в TUI после ротации.
- `__version__` → `0.6.0`.

### Производительность
- Hot path (mid-state hashlib) не тронут — бенчмарк на 4-х воркерах
  показал 3.18 MH/s (тот же порядок, что v0.5.0).
- ctypes-backend без mid-state ожидаемо медленнее (~0.3x от hashlib
  mid-state). Это сознательная плата за честный замер «голого»
  Python→C overhead.

## [0.5.0] — 2026-05-02

### Добавлено
- **Curses TUI-дашборд** (`tui.py`): `hope-hash --tui`. Постоянное окно с
  EMA-хешрейтом, аптаймом, шарами (sent/ok/rej), pool diff, текущим job_id,
  числом воркеров. Quit на `q`/`ESC`/`Ctrl+C`. На Windows без
  `windows-curses` graceful fallback (warning + продолжаем без TUI),
  чтобы не ломать `cli.main()` для тех, кто запустил с голым CPython.
- **`StatsProvider`** (`tui.py`): thread-safe шина между `mine()` и
  потребителями (TUI/healthz/web). Pure-Python, без curses-зависимостей.
- **`--no-banner`** и ASCII-логотип (`banner.py`): при старте печатается
  «HOPE HASH» в ASCII; для cron/systemd подавляется флагом.
- **Healthcheck endpoint** (`metrics.py`): `GET /healthz` отдаёт JSON
  `{status, uptime_s, last_share_ts, ...}`. `ok` (200) когда reader жив,
  EMA свежее 30с, шар в пределах `--healthz-stale-after` секунд.
  `degraded` (200) — что-то одно подвыпало. `down` (503) — reader умер.
  Внутри: чистая `build_health_snapshot()` тестируется без сети.
- **Telegram inbound-команды** (`notifier.py`, `tg_commands` встроены):
  long-poll `getUpdates` в фоновой нити, диспатч `/stats`, `/stop`,
  `/restart`, `/help`. Authz по `chat_id`. Включается через
  `HOPE_HASH_TELEGRAM_INBOUND=1` (по умолчанию off — чтобы не открывать
  поллер без явного opt-in).
- **`--log-file PATH`**: дублирует логи в файл. Особенно полезно с `--tui`,
  где stdout занят дашбордом.
- **Grafana-дашборд** (`deploy/grafana/hope-hash.json`): минимальный JSON
  для Grafana 10.x, datasource templated как `prometheus`. Панели:
  hashrate over time, pool diff, shares accepted vs rejected (stacked
  bar), workers gauge, uptime stat.
- **Type annotations** в `miner.py` и `stratum.py` доведены до 100%.
- **Тесты**: `test_tui.py` (StatsProvider, форматтеры), `test_banner.py`,
  `test_healthz.py` (snapshot + HTTP), `test_notifier_timing.py`
  (notify_share_accepted дёргается ТОЛЬКО из ack-callback, не из submit).
  Расширен `test_notifier.py` (inbound dispatch + chat_id authz).
  Всего **145 тестов** (было 101).

### Изменено
- `mine()` принимает опциональный `stats_provider: StatsProvider` —
  пушит EMA/job/share-события в общую шину, чтобы TUI и healthz видели
  одно и то же состояние.
- `supervisor_loop()` принимает опциональный `restart_event` — для
  обработчика `/restart` из Telegram.

## [0.4.0] — 2026-04-30

### Добавлено
- **Бенчмарк-режим** (`bench.py`): `hope-hash --benchmark [--bench-duration SEC]`.
  Меряет pure-Python хешрейт без сети и без шар. Baseline для будущих
  C/Rust/SIMD/GPU-оптимизаций (см. ROADMAP уровни 2–3). На Intel i7-12700H
  даёт ~570 KH/s на воркер с mid-state SHA-256.
- **Pre-flight валидация BTC-адреса** (`address.py`): bech32 (BIP-173),
  bech32m (BIP-350), Base58Check. Mainnet-only. Опечатки и testnet-адреса
  отвергаются локально, до сетевого round-trip к пулу.
- **Тесты для StratumClient** (`test_stratum.py`): 15 тестов протокольного
  слоя через FakeSocket-фикстуру (subscribe/authorize/notify/set_difficulty/
  set_extranonce/submit/reader_loop). Закрывает крупнейшую неоттестированную
  поверхность кода.
- Тест на demo-режим в spawn-подпроцессе.
- Тесты на адресную валидацию (18 шт).
- Тесты на бенчмарк (3 шт).
- Всего **101 тест** (было 64 до v0.3 audit tail).

### Изменено
- `parallel.stop_pool`: магическое 0.2с-окно drain-а заменено на
  drain-until-`queue.Empty` с safety-cap. К моменту вызова все находки
  уже в очереди или feeder-буфере воркера.
- `miner.supervisor_loop`: `logger.error()` → `logger.exception()` —
  unexpected ошибки теперь пишут полный traceback, а не молча
  маскируются однострочным логом.
- `miner.mine`: при потере шара (OSError на reconnect) лог теперь
  включает `job_id`/`nonce`/`hash` — оператор может найти запись в
  SQLite. Намеренно не ретраим: stale-share может привести к ban'у.
- `stratum.py`: добавлены return-type аннотации, `submit()` параметры
  типизированы, `_send` явно бросает OSError при `sock is None`.

### Исправлено
- `miner.mine`: проверка переполнения `extranonce2_counter` —
  `f"{counter:0{en2_size*2}x}"` без проверки молча обрубал старшие
  биты при `counter >= 2^(en2_size*8)`. Теперь wrap с warning-логом.

## [0.3.0] — 2026-04-30

### Добавлено
- **Mid-state SHA-256** (`parallel.py`): block header 80 байт = 64 + 16.
  Первые 64 байта — константа в пределах одного nonce-цикла. Pre-compute
  через `hashlib.sha256().copy()` даёт ≈×1.5–2 к хешрейту без зависимостей.
- **Demo-режим** (`demo.py`): `hope-hash --demo [--workers N] [--demo-diff DIFF]`.
  Запускается без подключения к пулу; ищет nonce для синтетического заголовка
  с низкой сложностью. Полезен для презентаций и offline-тестирования.
- **Vardiff** (`stratum.py`): метод `suggest_difficulty(diff)` и
  CLI-флаг `--suggest-diff FLOAT`. Отправляет `mining.suggest_difficulty`
  после авторизации, чтобы запросить у пула удобную сложность для CPU.
- 3 новых теста `TestMidstateSha256` в `test_block.py`. Всего **59 тестов**.

### Исправлено
- `miner.py`: голый `except Exception: pass` на `found_queue.get_nowait()`
  заменён на `except queue.Empty:` — реальные ошибки больше не маскируются.
- `parallel.py`: аналогичный fix в `stop_pool` при drain-е очереди.
- `miner.py`, `parallel.py`: `time.time()` → `time.perf_counter()` для
  всех относительных интервалов (EMA, alive-check, drain-deadline).
  Защищает от ложных скачков при корректировке системных часов (NTP).

## [0.2.0] — 2026-04-30

### Добавлено
- **Multiprocessing** (`parallel.py`): N воркеров делят nonce-пространство
  `[0, 2³²)` равными долями. CLI-флаг `--workers N` (default `cpu_count - 1`).
  `found_queue` для шаров, `hashes_counter` для статистики.
- **EMA-хешрейт**: скользящее среднее (alpha=0.3, окно 5с) вместо мгновенного.
- **SQLite-журнал** (`storage.py`): таблицы `shares` и `sessions`, WAL-режим,
  потокобезопасность. CLI-флаги `--db PATH`, `--no-db`.
- **Prometheus-экспортёр** (`metrics.py`): `/metrics` на `http.server`, метрики
  `hopehash_shares_total`, `hopehash_hashrate_hps`, `hopehash_pool_difficulty`,
  `hopehash_workers`, `hopehash_uptime_seconds`. CLI-флаг `--metrics-port`
  (default 9090, 0 — выключить).
- **Telegram-уведомления** (`notifier.py`): через stdlib `urllib`, без
  `python-telegram-bot`. Конфиг через env: `HOPE_HASH_TELEGRAM_TOKEN`,
  `HOPE_HASH_TELEGRAM_CHAT_ID`. События: started / stopped / share_accepted /
  block_found.
- 41 новый юнит-тест (storage: 9, metrics: 16, notifier: 16). Всего **56 тестов**.

### Изменено
- `mine()` принимает опциональные `store`, `metrics`, `notifier` (None — disabled).
- `__init__.py` re-export `ShareStore`, `Metrics`, `MetricsServer`, `TelegramNotifier`.
- `.gitignore` дополнен `*.db`, `*.db-journal`, `*.db-wal`, `*.db-shm`.

## [0.1.0] — 2026-04-30

### Добавлено
- Stratum V1 клиент к `solo.ckpool.org:3333` (TCP + JSON-RPC).
- Цикл хеширования SHA-256 на чистом stdlib.
- Обработка `mining.set_difficulty`, `mining.notify`, `mining.set_extranonce`.
- Reconnect с экспоненциальным backoff (1→60с).
- Общий `stop_event` для чистого shutdown по Ctrl+C.
- Логирование через `logging` со стандартными уровнями.
- 15 юнит-тестов на криптографические функции (`unittest`).
- Реструктуризация в `src/`-layout с пакетом `hope_hash`.

[Unreleased]: https://github.com/devAsmodeus/Hope-Hash/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/devAsmodeus/Hope-Hash/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/devAsmodeus/Hope-Hash/releases/tag/v0.1.0
