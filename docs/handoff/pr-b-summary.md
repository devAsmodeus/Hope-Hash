# PR B summary — `feat/perf-and-resilience` (v0.6.0)

Multi-pool failover + solo `getblocktemplate` + ctypes SHA-256 backend
+ bench `--backends`. Pure stdlib (`ctypes` + `urllib`). Tests:
**145 → 225** (+80).

## File map

### Added

| Path | Purpose |
| --- | --- |
| `src/hope_hash/pools.py` | `PoolList` (round-robin failover, дедуп, `mark_failed/success/rotate/full_cycle_failed`), `parse_pool_spec()`. |
| `src/hope_hash/sha_native.py` | ctypes-обёртка над libcrypto EVP API (`sha256`, `sha256d`, `is_available`, `BACKEND_NAME`). Загружает `libcrypto-3.dll` / `libcrypto.so.3` / `/opt/homebrew/lib/libcrypto.dylib` и т.п. Fallback на `hashlib` при отсутствии. |
| `src/hope_hash/solo.py` | `SoloClient` (имитация `StratumClient` поверх JSON-RPC), `BitcoinRPC`, `build_coinbase`, `compute_witness_commitment`, `parse_default_witness_commitment`, `serialize_block`, `compute_merkle_root_from_txids`, `_varint`/`_push_data`/`_serialize_height`. |
| `tests/test_pools.py` | 24 теста: парсер spec, дедуп, ротация на пороге, wrap-around, single-pool no-op, `full_cycle_failed`/`reset_round`, нет deadlock'ов. |
| `tests/test_sha_native.py` | 12 тестов: `is_available()` tolerant, паритет `sha256`/`sha256d` с `hashlib` на пустых/коротких/длинных/binary векторах, паритет на 80-байтном block header. |
| `tests/test_solo.py` | 35 тестов: `_varint`, `_push_data`, `_serialize_height`, `build_coinbase` (с/без extranonce, с/без witness commitment), `compute_witness_commitment`, `parse_default_witness_commitment`, `compute_merkle_root_from_txids`, `serialize_block`, `BitcoinRPC` auth, `SoloClient` через `FakeRPC` (connect/job/submit success+reject). |
| `tests/test_bench_backends.py` | 9 тестов: `available_backends()` всегда содержит hashlib первым, `ctypes` only-if-available, `run_benchmark_all_backends()` smoke. |
| `docs/handoff/pr-b-summary.md` | This file. |

### Modified

| Path | Why |
| --- | --- |
| `src/hope_hash/cli.py` | Новые флаги: `--pool`, `--rotate-after-failures`, `--sha-backend`, `--backends`, `--solo`, `--rpc-url/--rpc-cookie/--rpc-user/--rpc-pass`, `--solo-poll-sec`. Хелперы `_resolve_sha_backend()`, `_build_pool_list()`. Solo-ветка собирает `SoloClient`+`BitcoinRPC`. Pool-ветка строит `PoolList` и передаёт в supervisor через kwargs. |
| `src/hope_hash/miner.py` | `supervisor_loop()` теперь принимает `pools: Optional[PoolList]` и `stats_provider: Optional[StatsProvider]`. На неудавшемся коннекте — `mark_failed()`, после порога — ротация + `set_endpoint()` без пересоздания клиента. После `full_cycle_failed()` — обычный exponential backoff. `mine()` принимает `sha_backend` и пробрасывает в `start_pool()`. |
| `src/hope_hash/parallel.py` | `worker()` теперь диспатчит на `_worker_hashlib_midstate()` (без изменений в hot path) или `_worker_ctypes()` (sha256d через libcrypto, без mid-state). `start_pool()` принимает `sha_backend`. |
| `src/hope_hash/stratum.py` | `set_endpoint(host, port)` — перенацеливание клиента без пересоздания, локи/`on_share_result`/`username` сохраняются. |
| `src/hope_hash/bench.py` | `run_benchmark()` принимает `sha_backend` и `print_header`. Новый `run_benchmark_all_backends()` + `available_backends()`. Финальная строка `[bench] result: ctypes X MH/s (Yx vs hashlib-midstate)`. |
| `src/hope_hash/tui.py` | `StatsProvider.update_pool(url)` — для multi-pool отображения. |
| `src/hope_hash/__init__.py` | Версия → `0.6.0`. Re-export `PoolList`, `parse_pool_spec`, `BitcoinRPC`, `RPCError`, `SoloClient`, `build_coinbase`, `compute_witness_commitment`, `parse_default_witness_commitment`, `serialize_block`, `available_backends`, `run_benchmark_all_backends`, `sha_native`. |
| `CHANGELOG.md` | v0.6.0 секция. |
| `README.md` | "Что нового в v0.6.0" параграф (НЕ переписывал README — это PR C). |
| `ROADMAP.md` | Тикнул failover, getblocktemplate, ctypes-обёртка SHA-256. |

## New CLI flags

| Flag | Default | Notes |
| --- | --- | --- |
| `--pool HOST:PORT` | `solo.ckpool.org:3333` | Repeatable. Если задан, дефолтный CKPool игнорируется. |
| `--rotate-after-failures N` | `3` | Сколько подряд провалов до ротации. |
| `--sha-backend {auto,hashlib,ctypes}` | `auto` | auto = ctypes если libcrypto загружается. |
| `--backends` | off | С `--benchmark`: прогон всех доступных backend'ов. |
| `--solo` | off | Solo-mode через `getblocktemplate`. |
| `--rpc-url URL` | none | Обязателен с `--solo`. |
| `--rpc-cookie PATH` | none | Cookie wins над `--rpc-user/--rpc-pass`. |
| `--rpc-user USER` | none | Альтернатива cookie. |
| `--rpc-pass PASS` | none | Альтернатива cookie. |
| `--solo-poll-sec SEC` | `5.0` | Период `getblocktemplate`. |

## New endpoints

Нет новых сетевых эндпоинтов (это PR C).

## Architecture additions

- **`PoolList`** хранит endpoint'ы, индекс текущего, счётчики провалов
  и аккумулятор «ротаций с момента успеха» (для `full_cycle_failed`).
  `RLock`, чтобы вложенные методы не deadlock'ались.
- **`set_endpoint(host, port)`** на `StratumClient` сохраняет
  `on_share_result`, `stop_event`, `suggest_diff`, `username` и локи.
  Это критично: `mine()` уже подписан на `client.on_share_result`,
  пересоздание клиента сбросило бы callback.
- **`SoloClient`** — fully duck-typed под `StratumClient`. Любой код,
  который читает `current_job`/`job_lock`/`extranonce1`/`difficulty`/
  `submit()`/`on_share_result`, работает без изменений.
- **`worker()` диспатчит** на private `_worker_*` функции по `sha_backend`.
  Hot path mid-state остался байт-в-байт тем же; ctypes — отдельная
  ветка для бенча.

## Gotchas for PR C

1. **`SoloClient.host`/`port` в `(solo)`/0** — healthz-зонд из PR A
   (`client.sock is not None`) работает: после `connect()` мы кладём
   sentinel в `sock`. Web-дашборд из PR C должен показывать
   `pool_url` из `StatsProvider`, не `client.host` напрямую.
2. **`build_coinbase` использует OP_RETURN как scriptPubKey** — это
   сознательное упрощение (учебный код, шанс найти блок ≈ 0). Если
   PR C захочет «настоящий» payout-скрипт, нужно реализовать
   bech32/base58check decode → P2WPKH/P2PKH. Это отдельная задача
   с тестовыми векторами BIP-173/BIP-350.
3. **`parse_default_witness_commitment` ожидает префикс**
   `6a24aa21a9ed...` — стандарт bitcoind. Если PR C добавит regtest
   с кастомным форматом, надо это учитывать.
4. **`SoloClient.reader_loop` выходит при первой RPC-ошибке**, чтобы
   supervisor переподключился. На стабильном bitcoind это значит —
   no-op (poll_loop успешный). На flaky сети будет много reconnect'ов.
   Если станет больно — нужно ввести retry с backoff внутри reader_loop.
5. **`--sha-backend ctypes` медленнее** (~0.3x от hashlib mid-state)
   потому что без mid-state. Web-дашборд должен это понимать —
   показывать `BACKEND_NAME` рядом с хешрейтом, чтобы пользователь
   не удивлялся, почему "ctypes медленнее".
6. **PoolList дедуп case-insensitive** по host. Если кто-то указал
   и `pool.com:3333` и `Pool.Com:3333` — это один endpoint.
7. **`/api/stats` (PR C)** должен включать `current_pool` (есть в
   `StatsSnapshot.pool_url`) и `sha_backend` (можно добавить отдельный
   геттер `StatsProvider.set_backend(name)` или передать константой
   при инициализации).

## Open questions for PR C

- Web-дашборд: показывать ли отдельно «pool failures» из `PoolList`?
  Можно сделать `PoolList.failures_summary() -> dict[str, int]` для
  `/api/stats` — будет видно, что один из пулов вечно падает.
- Solo-режим в `/healthz`: `last_share_ts` теряет смысл (шар = блок,
  ≈ 0 за всю жизнь майнера). Стоит добавить `last_block_template_ts`
  отдельным полем — это видно через `SoloClient._last_template`-timestamp.
- `--sha-backend ctypes` мониторим через `BACKEND_NAME`, но в
  `Metrics`/Prometheus его пока нет. Добавить `hopehash_sha_backend`
  как label на `hopehash_hashrate_hps`?
- Docker (PR C): `libcrypto` доступен в `python:3.11-slim` — auto
  должен сразу выбрать ctypes. Стоит ли добавить env var
  `HOPE_HASH_SHA_BACKEND` для контейнерной конфигурации?
- BIP-22 `coinbasetxn`: некоторые ноды могут отдавать готовый coinbase
  через `coinbasetxn` вместо `coinbasevalue`. PR B этот путь не
  поддерживает (всегда строим сами). Если PR C хочет совместимости
  с большим числом нод — это TODO.

## Verification

```
py -3.11 -m unittest discover -s tests -v
# Ran 225 tests in ~14s — OK

py -3.11 -m hope_hash --help                                   # CLI парсится
py -3.11 -m hope_hash --benchmark --bench-duration 1 --backends  # сравнение backend'ов
py -3.11 -m hope_hash --benchmark --bench-duration 3 --workers 4
# hashrate: 3.18 MH/s  (тот же порядок, что v0.5.0 — hot path не тронут)
```

No `pip install`. No third-party imports under `src/hope_hash/`.
`block.py` endianness и `_worker_hashlib_midstate` hot path не
изменились. Existing 145 тестов остались зелёными.
