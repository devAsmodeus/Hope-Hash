# Архитектура

Hope-Hash намеренно маленький. Этот документ — карта файлов и нитей плюс
обоснования решений, которые сначала выглядят странно.

## Краткое описание протокола

Stratum V1 (line-delimited JSON поверх TCP) для pool-режима; Bitcoin Core
JSON-RPC `getblocktemplate` / `submitblock` (BIP-22 / BIP-23) для
solo-режима. Оба производят одинаковый внутренний `current_job` dict,
поэтому `mine()` всё равно, откуда пришла работа.

Layout block header (80 байт, little-endian кроме отмеченного):

```
4   version            (LE)
32  prev_hash          (word-swap, см. block.swap_words)
32  merkle_root        (raw double-SHA-256 output)
4   ntime              (LE)
4   nbits              (LE)
4   nonce              (LE, единственное поле, которое мы перебираем)
```

Перевороты version/ntime/nbits и word-swap для prev_hash — не косметика,
они проверены против реальных mainnet-блоков. Не «причёсывать»: см.
инварианты в `CLAUDE.md`.

## Threading-модель

Один процесс плюс опционально N multiprocessing-worker детей. В главном
процессе:

| Нить | Владелец | Жизненный цикл | Что делает |
| --- | --- | --- | --- |
| main | `cli.main` | весь run | парсит argparse, крутит `mine()`, ловит `Ctrl+C`. |
| `stratum-supervisor` / `solo-supervisor` | `miner.supervisor_loop` | весь run | reconnect с экспоненциальным backoff; ротация пулов на фейле; `restart_event`. |
| `stratum-reader` | `StratumClient.reader_loop` | per-connection | читает JSON-строки, диспатчит `mining.notify` / `set_difficulty` / submit-ответы. **Не** daemon — хотим чисто join'нуть на Ctrl+C. |
| `hope_hash-tui` | `tui.TUIApp` | опц. | curses-дашборд, daemon. |
| `hope_hash-metrics-NNNN` | `metrics.MetricsServer` | опц. | Prometheus `/metrics` и `/healthz`, daemon. |
| `hope_hash-webui-NNNN` | `webui.WebUIServer` | опц. | HTML / `/api/stats` / `/api/events`, daemon. |
| `telegram-out` / `telegram-in` | `notifier.TelegramNotifier` | опц. | outbound queue worker + (opt-in) long-poll для inbound-команд. |

Воркер-дети — `multiprocessing` (spawn-safe), nonce-цикл в
`parallel.worker`.

## Общее состояние

- `client.current_job` — под `client.job_lock`. Reader пишет, miner
  читает. Miner перепроверяет job ID каждые ~16k хешей, чтобы свежий
  `mining.notify` быстро рвал nonce-цикл.
- `client.stop_event` — универсальный kill switch. Любая нить,
  увидевшая его, должна сворачиваться: supervisor перестаёт
  переподключаться, `mine()` возвращается, `reader_loop` выходит.
- `StatsProvider` — каноничная шина данных для внешних наблюдателей
  (TUI, `/healthz`, `/metrics`, web). Доступ через `threading.Lock`.
  `subscribe()` подписывает callback на SSE-события.

## Карта файлов

| Файл | Роль |
| --- | --- |
| `block.py` | Чистые функции: `double_sha256`, `swap_words`, `difficulty_to_target`, `build_merkle_root`. Без сайд-эффектов, под тестами. |
| `address.py` | Валидация mainnet BTC-адресов: BIP-173 (bech32), BIP-350 (bech32m), Base58Check. |
| `stratum.py` | `StratumClient` (TCP + JSON-RPC). `set_endpoint()` для multi-pool failover. |
| `pools.py` | `PoolList` round-robin failover, дедуп, `mark_failed/success`. |
| `solo.py` | `SoloClient` (duck-typed `StratumClient`), `BitcoinRPC`, билдеры coinbase + witness commitment. |
| `parallel.py` | Диспатч воркеров (`hashlib` mid-state vs `ctypes` libcrypto), `start_pool`, `stop_pool`. |
| `sha_native.py` | Опциональный `ctypes`-обёртка над libcrypto (`libcrypto-3.dll` / `.so.3` / `.dylib`). |
| `miner.py` | `mine()` оркестратор, `supervisor_loop`, `_build_header_base`. |
| `bench.py` | Микробенчмарк, `--backends` matrix runner. |
| `demo.py` | Offline-режим против синтетического заголовка. |
| `storage.py` | SQLite share/session журнал (WAL). |
| `metrics.py` | Prometheus-экспортёр и `/healthz`. |
| `notifier.py` | Telegram outbound + opt-in inbound long-poll. |
| `tui.py` | `StatsProvider`, `StatsSnapshot`, curses `TUIApp`, форматтеры. |
| `webui.py` | Stdlib `http.server` дашборд: HTML, `/api/stats`, `/api/events` (SSE), `/healthz`. |
| `banner.py` | ASCII-баннер. |
| `cli.py` | Argparse, разводка observers, lifecycle. |

## Hot path

`parallel._worker_hashlib_midstate`. 80-байтовый header — это `64 + 16`,
SHA-256 absorbs 64-байтовыми блоками, поэтому первый блок — константа в
рамках nonce-цикла. Делаем `hashlib.sha256().copy()` после первого
absorb и кормим только финальные 16 байт на каждый nonce. На практике
~×1.5 ускорение по сравнению с наивным double-SHA-256.

Любая аллокация или ветвление внутри этого цикла убивает хешрейт.
Меряй каждое изменение через `--benchmark --bench-duration 10` против
предыдущего коммита.

## Observers

Все опциональны, все включаются CLI-флагами или env vars. Никто из них
не мутирует state майнера — только читает `StatsProvider` и пишет в
свой sink (HTTP, SQLite, сеть):

- `--db PATH` → `ShareStore` (SQLite, WAL).
- `--metrics-port N` → `MetricsServer` (`/metrics`, `/healthz`).
- `--web-port N` → `WebUIServer` (HTML, `/api/stats`, `/api/events`).
- `--tui` → `TUIApp`.
- `HOPE_HASH_TELEGRAM_*` → `TelegramNotifier`.

## Заметки про производительность

Потолок pure-Python SHA-256 на современном CPU — ~0.5–1 MH/s на воркер.
Mid-state-кэш доводит до этого потолка. ctypes-backend платит overhead
Python→C, превышающий саму стоимость SHA-256, поэтому он **медленнее**
mid-state hashlib для майнинга; он существует для бенчмарк-матрицы и
честного сравнения с будущими C/Rust расширениями.

## Trade-off ctypes-backend

`sha_native.py` пробует `libcrypto-3.dll` / `libcrypto.so.3` /
`/opt/homebrew/lib/libcrypto.dylib` через `ctypes.CDLL`. Если ни один не
загрузился — тихо fallback на `hashlib`. Произвольные load-пути из env
не разрешаем — список зашит в коде.

## Solo-режим caveats

`SoloClient` поллит `getblocktemplate` и собирает coinbase с push'ом
BIP-34 height и (когда bitcoind отдаёт `default_witness_commitment`)
BIP-141 witness commitment. Payout-script — заглушка OP_RETURN; для
реального блока нужен настоящий P2WPKH/P2PKH. Это сознательная
учебная граница; обоснование — в handoff PR-B.

## Ссылки

- BIP-22 / BIP-23 — `getblocktemplate`.
- BIP-34 — height в coinbase scriptSig.
- BIP-141 — segwit / witness commitment.
- BIP-173 / BIP-350 — bech32 / bech32m.
- Stratum V1 — [референс от Braiins](https://braiins.com/stratum-v1/docs).

## См. также

- [`architecture.en.md`](architecture.en.md) — английская версия.
- [`getting-started.ru.md`](getting-started.ru.md) — первый запуск.
- [`deploy.ru.md`](deploy.ru.md) — Docker, Prometheus, Grafana.
