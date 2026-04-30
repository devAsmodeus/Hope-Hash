# Changelog

Все значимые изменения проекта отражены здесь.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект придерживается [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

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
