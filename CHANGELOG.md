# Changelog

Все значимые изменения проекта отражены здесь.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект придерживается [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

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
