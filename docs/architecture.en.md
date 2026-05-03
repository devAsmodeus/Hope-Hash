# Architecture

Hope-Hash is intentionally small. This document is a map of every file and
every thread, plus the rationale for the choices that look weird at first
sight.

## Protocol summary

Stratum V1 (line-delimited JSON over TCP) for pool mode; Bitcoin Core
JSON-RPC `getblocktemplate` / `submitblock` (BIP-22 / BIP-23) for solo
mode. Both produce the same internal `current_job` dict, so `mine()` does
not care where the work came from.

Block header layout (80 bytes, little-endian unless noted):

```
4   version            (LE)
32  prev_hash          (word-swapped, see block.swap_words)
32  merkle_root        (raw double-SHA-256 output)
4   ntime              (LE)
4   nbits              (LE)
4   nonce              (LE, the only field we vary)
```

The version / ntime / nbits flips and the prev_hash word-swap are not
cosmetic — they were verified against real mainnet blocks. Do not "clean
them up": see `CLAUDE.md` invariants.

## Threading model

One process plus, optionally, N multiprocessing worker children. In the
main process:

| Thread | Owner | Lifetime | What it does |
| --- | --- | --- | --- |
| main | `cli.main` | the whole run | parses args, runs `mine()` (the hot orchestration loop), handles `Ctrl+C`. |
| `stratum-supervisor` / `solo-supervisor` | `miner.supervisor_loop` | the whole run | reconnect with exponential backoff; rotate pools on failure; honour `restart_event`. |
| `stratum-reader` | `StratumClient.reader_loop` | per-connection | read JSON lines, dispatch `mining.notify` / `set_difficulty` / submit replies. **Not** a daemon — we want to join cleanly on Ctrl+C. |
| `hope_hash-tui` | `tui.TUIApp` | optional | curses dashboard, daemon. |
| `hope_hash-metrics-NNNN` | `metrics.MetricsServer` | optional | Prometheus `/metrics` and `/healthz`, daemon. |
| `hope_hash-webui-NNNN` | `webui.WebUIServer` | optional | HTML / `/api/stats` / `/api/events`, daemon. |
| `telegram-out` / `telegram-in` | `notifier.TelegramNotifier` | optional | outbound queue worker + (opt-in) long-poll for inbound commands. |

Worker children come from `multiprocessing` (spawn-safe) and run the
nonce loop in `parallel.worker`.

## Shared state

- `client.current_job` — guarded by `client.job_lock`. Reader writes,
  miner reads. The miner re-checks the job ID every ~16k hashes so a
  fresh `mining.notify` interrupts the nonce loop fast.
- `client.stop_event` — the universal kill switch. Any thread that sees
  it set must wind down: the supervisor stops reconnecting, `mine()`
  returns, `reader_loop` exits.
- `StatsProvider` — the canonical data bus for outside observers (TUI,
  `/healthz`, `/metrics`, web). All access goes through a `threading.Lock`.
  `subscribe()` adds a callback for SSE events.

## File map

| File | Role |
| --- | --- |
| `block.py` | Pure functions: `double_sha256`, `swap_words`, `difficulty_to_target`, `build_merkle_root`. No side effects, fully unit-tested. |
| `address.py` | Mainnet BTC address validation: BIP-173 (bech32), BIP-350 (bech32m), Base58Check. |
| `stratum.py` | `StratumClient` (TCP + JSON-RPC). `set_endpoint()` for multi-pool failover. |
| `pools.py` | `PoolList` round-robin failover, deduplication, `mark_failed/success`. |
| `solo.py` | `SoloClient` (duck-typed `StratumClient`), `BitcoinRPC`, coinbase + witness commitment builders. |
| `parallel.py` | Worker dispatch (`hashlib` mid-state vs `ctypes` libcrypto), `start_pool`, `stop_pool`. |
| `sha_native.py` | Optional `ctypes` wrapper around libcrypto (`libcrypto-3.dll` / `.so.3` / `.dylib`). |
| `miner.py` | `mine()` orchestrator, `supervisor_loop`, `_build_header_base`. |
| `bench.py` | Microbenchmark, `--backends` matrix runner. |
| `demo.py` | Offline mode against a synthetic header. |
| `storage.py` | SQLite share / session journal (WAL mode). |
| `metrics.py` | Prometheus exporter and `/healthz`. |
| `notifier.py` | Telegram outbound + opt-in inbound long-poll. |
| `tui.py` | `StatsProvider`, `StatsSnapshot`, curses `TUIApp`, formatters. |
| `webui.py` | Stdlib `http.server` dashboard: HTML, `/api/stats`, `/api/events` (SSE), `/healthz`. |
| `banner.py` | ASCII banner. |
| `cli.py` | Argparse, observer wiring, lifecycle. |

## Hot path

`parallel._worker_hashlib_midstate`. The 80-byte header is `64 + 16` —
SHA-256 absorbs in 64-byte blocks, so the first block is constant within
a nonce loop. We `hashlib.sha256().copy()` after the first absorb and
only feed the trailing 16 bytes per nonce. Empirically ~1.5× speedup
versus naïve double-SHA-256.

Anything in this loop that allocates or branches kills the hashrate.
Benchmark every change with `--benchmark --bench-duration 10` against
the previous commit.

## Observers

All optional, all driven by either CLI flags or env vars. None of them
mutates miner state — they only read `StatsProvider` and write to their
own sinks (HTTP, SQLite, network):

- `--db PATH` → `ShareStore` (SQLite, WAL).
- `--metrics-port N` → `MetricsServer` (`/metrics`, `/healthz`).
- `--web-port N` → `WebUIServer` (HTML, `/api/stats`, `/api/events`).
- `--tui` → `TUIApp`.
- `HOPE_HASH_TELEGRAM_*` → `TelegramNotifier`.

## Performance notes

Pure-Python SHA-256 ceiling on a modern CPU is ~0.5–1 MH/s per worker.
Mid-state caching brings it close to the upper bound. The `ctypes`
backend pays a Python→C overhead that exceeds the SHA-256 cost itself,
so it is slower than mid-state hashlib for mining; it exists for the
benchmark matrix and for honest comparison with future C / Rust
extensions.

## ctypes backend trade-off

`sha_native.py` tries `libcrypto-3.dll` / `libcrypto.so.3` /
`/opt/homebrew/lib/libcrypto.dylib` etc. via `ctypes.CDLL`. If none load,
the backend silently falls back to `hashlib`. We do not allow arbitrary
load paths from environment input — the search list is hardcoded.

## Solo mode caveats

`SoloClient` polls `getblocktemplate` and assembles the coinbase with
BIP-34 height push and (when bitcoind exposes
`default_witness_commitment`) a BIP-141 witness commitment. The payout
script is a placeholder OP_RETURN — finding a real block needs a proper
P2WPKH / P2PKH script. This is intentional educational scope; see the
PR-B handoff for the rationale.

## References

- BIP-22 / BIP-23 — `getblocktemplate`.
- BIP-34 — coinbase height in scriptSig.
- BIP-141 — segwit / witness commitment.
- BIP-173 / BIP-350 — bech32 / bech32m.
- Stratum V1 — see [Braiins's reference](https://braiins.com/stratum-v1/docs).

## See also

- [`architecture.ru.md`](architecture.ru.md) — Russian version.
- [`getting-started.en.md`](getting-started.en.md) — first run.
- [`deploy.en.md`](deploy.en.md) — Docker, Prometheus, Grafana.
