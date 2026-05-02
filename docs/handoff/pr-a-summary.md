# PR A summary — `feat/ops-and-ux` (v0.5.0)

Closes Level 1 remainder + observability tail. Pure stdlib. No new pip
dependencies. Tests: **101 → 145** (44 new).

## File map

### Added

| Path | Purpose |
| --- | --- |
| `src/hope_hash/banner.py` | ASCII-art logo (`render_banner`, `print_banner`). Called from `cli.main()` unless `--no-banner`. |
| `src/hope_hash/tui.py` | `StatsProvider` (thread-safe data bus), `StatsSnapshot`, `TUIApp` (curses), `format_rate`/`format_uptime`, `is_curses_available()`. |
| `deploy/grafana/hope-hash.json` | Importable Grafana 10.x dashboard. Datasource templated as `${DS_PROMETHEUS}`. Panels: hashrate ts, pool diff, shares stacked bar, workers stat, uptime stat. |
| `tests/test_banner.py` | 6 tests: render non-empty, multiline, contains version, ASCII-only. |
| `tests/test_tui.py` | 13 tests: provider thread safety, snapshot immutability, share counters, format helpers. |
| `tests/test_healthz.py` | 11 tests: `build_health_snapshot` matrix + live HTTP `/healthz` smoke. |
| `tests/test_notifier_timing.py` | 5 tests: notify_share_accepted fires only from pool-ack path, never from submit. |
| `docs/handoff/pr-a-summary.md` | This file. |

### Modified

| Path | Why |
| --- | --- |
| `src/hope_hash/cli.py` | Added flags `--tui`, `--no-banner`, `--log-file`, `--healthz-stale-after`. Wired stats provider, TUI, healthz, telegram inbound. New helpers `_setup_logging_for_tui`, `_format_stats_message`. |
| `src/hope_hash/miner.py` | `mine()` accepts `stats_provider`, pushes hashrate/job/share events. `supervisor_loop()` accepts `restart_event` for `/restart` command. |
| `src/hope_hash/metrics.py` | Added `/healthz` route, `MetricsServer.set_health_provider()`, pure `build_health_snapshot()`. Handler factory now takes a mutable container so provider can be swapped after `start()`. |
| `src/hope_hash/notifier.py` | Inbound long-poll thread (`start_inbound`/`stop_inbound`), `register_command`, `_handle_update`, `_fetch_updates`. Authz by `chat_id`. Sentinel env var `HOPE_HASH_TELEGRAM_INBOUND`. |
| `src/hope_hash/stratum.py` | `from __future__ import annotations`, attribute type hints, `params: list[Any]` in `_send`, `dict[str, Any]` in `_handle_message`. |
| `src/hope_hash/__init__.py` | Bump version to `0.5.0`, re-export new symbols. |
| `tests/test_notifier.py` | +7 tests for inbound dispatch & chat_id authz. |
| `CHANGELOG.md` | v0.5.0 section. |
| `README.md` | "Что нового в v0.5.0" paragraph; test count 101 → 145. |
| `ROADMAP.md` | Ticked TUI, banner, telegram inbound, Grafana, healthchecks. |

## New CLI flags

| Flag | Default | Notes |
| --- | --- | --- |
| `--tui` | off | curses dashboard. Windows graceful skip if `windows-curses` not installed. |
| `--no-banner` | off | suppress ASCII banner (cron/systemd). |
| `--log-file PATH` | none | duplicate logs to file (essential with `--tui`). |
| `--healthz-stale-after SEC` | 600 | window after which `/healthz` flips to `degraded`. |

## New env vars

| Name | Values | Purpose |
| --- | --- | --- |
| `HOPE_HASH_TELEGRAM_INBOUND` | `1`/`true`/`yes`/`on` | opt-in for the long-poll command thread. Default off so we don't spawn a network thread without explicit ack. |

## New endpoints

- `GET /healthz` on the existing `--metrics-port`. JSON. 200 for `ok`/`degraded`, 503 for `down`. Schema:
  ```
  {"status": "ok|degraded|down",
   "reason": "...|null",
   "uptime_s": float,
   "hashrate_ema": float,
   "hashrate_ts": float|null,
   "last_share_ts": float|null}
  ```

## Architecture additions

- `StatsProvider` is the canonical data bus. `mine()` pushes; consumers
  read. Used today by TUI and `/healthz`. Will be used by `/api/stats`
  in PR C (web). All access is `threading.Lock`-guarded.
- `MetricsServer.set_health_provider(callable -> dict)` — register a
  callable that returns a dict (must include `status`). Handler reads
  this on every request via a one-slot mutable container, so the
  provider can be swapped after `start()`.
- Telegram inbound is a separate daemon thread inside `TelegramNotifier`.
  It does NOT share queue with outbound — only acks land in outbound
  queue. Authz happens before dispatch (chat_id mismatch → warning log,
  drop the update).

## Gotchas for PR B

1. **Don't change `mine()` signature** without preserving keyword-only
   compatibility — it's now public-API-shaped (positional + kwargs).
   PR B's solo mode and multi-pool will likely want to wrap mine in a
   different way; prefer a thin façade module over modifying `mine()`.
2. **`StatsProvider` is the canonical data bus.** When you add multi-pool
   failover, push the active pool URL via `provider._snap.pool_url`
   under the lock (or add a `update_pool(url)` method). The TUI shows it.
3. **Healthz reader_alive heuristic** in `cli.py` checks
   `client.sock is not None and supervisor.is_alive()`. With multi-pool
   failover the meaning of "current client" changes — you may need to
   pass a callable that knows about the active connection.
4. **Telegram `/stop` calls `client.close()` directly** to wake reader.
   With multi-pool the close target is whatever connection is current;
   keep the indirection through the supervisor.
5. **Curses on Windows** silently degrades. Don't add try/except around
   `import curses` elsewhere — use `tui.is_curses_available()` instead
   to keep the branch test points consolidated.
6. **`cli.main()` monkey-patches `stats_provider.update_hashrate`** to
   capture the timestamp for healthz. This is intentional (avoids
   threading another arg through `mine()`) but if you refactor it,
   move the hashrate-ts bookkeeping into `StatsProvider` itself
   (e.g. `last_hashrate_ts` field) and read it from healthz directly.
7. **Notifier inbound thread is daemon=True.** On hard kill it dies
   silently. `shutdown()` is the clean path. Don't add network calls
   in handler functions — they execute in the inbound thread and a
   long urlopen will block subsequent commands.
8. **Test isolation:** `test_healthz.py` binds free ports and starts
   real HTTP servers. On busy CI matrix this can flake if a port is
   grabbed in the gap between `_free_port()` and `bind`. We accept
   this — see the comment in `_free_port`.

## Open questions for PR B

- Should `/restart` also re-init the stats provider? Currently it only
  bounces the TCP connection, hashrate EMA continues. Probably correct,
  but worth a confirm.
- Should `/healthz` know about multi-pool? E.g. degraded if all pools
  down, ok if at least one is up. Spec says yes — design the provider
  callable to take a list of clients in PR B.
- Telegram inbound: should `/stats` include the active pool name when
  multi-pool ships? Recommend yes — the user will care.
- ctypes SHA-256 backend: when it lands, the bench mode comparison
  matrix in `bench.py` should pull from `StatsProvider` so the TUI
  can show "active backend" too.
- Solo mode (`getblocktemplate`): the healthz `last_share_ts` field
  loses meaning when there's no pool to send to. Add a separate
  `last_block_template_ts` for the solo path.

## Verification

```
py -3.11 -m unittest discover -s tests -v
# Ran 145 tests in ~10s — OK
py -3.11 -m hope_hash --help                  # CLI parses
py -3.11 -m hope_hash --benchmark --bench-duration 1 --workers 1   # banner + bench
```

No `pip install` was performed. No third-party imports added under
`src/hope_hash/`. All hot-path code in `parallel.py`/`block.py` left
untouched.
