# 2026-05-02 — Three-PR ROADMAP push: ops, perf, web+docs

## Context

Hope-Hash is at v0.4.0 (benchmark mode). ROADMAP Levels 1.5/2/3 mostly open. Goal:
ship the realistic, **stdlib-only** slice of Levels 1–2 plus web + final docs in three
sequential, stacked PRs, executed by subagents, then reviewed by a separate panel.

Hard constraint from `CLAUDE.md`: **no new runtime dependencies**. Anything that
requires a third-party package (`rich`, `FastAPI`, `cffi`, `cupy`, `PyO3`) is out
of scope. Where ROADMAP asked for `rich` we use `curses`; where it asked for
FastAPI we use stdlib `http.server`; where it asked for `cffi` we use `ctypes`.

## Scope: three stacked PRs

### PR A — `feat/ops-and-ux` (off `main`)

Closes Level 1 remainder + tail audit items.

**Features**
- `hope-hash --tui` — curses dashboard (hashrate EMA, uptime, shares accepted/rejected,
  current job_id, pool diff, workers). Updates in place. Quit on `q` or Ctrl+C.
- ASCII-art logo printed once at startup (gated by `--no-banner` for log-only mode).
- Telegram inbound commands: `/stats` (returns last EMA + uptime + counters),
  `/stop` (sets stop_event), `/restart` (re-runs supervisor). Long-poll via
  `urllib`, single background thread, idempotent disable when env vars absent.
- Healthcheck endpoint `/healthz` on the metrics HTTP server. Returns 200 with
  JSON `{status, uptime_s, last_share_ts}` when stratum reader is alive AND
  last hashrate sample is non-zero, 503 otherwise.
- `deploy/grafana/hope-hash.json` — importable dashboard with hashrate/diff/shares
  panels referencing the existing Prometheus metrics.

**Quality**
- Test for `notify_share_accepted` timing (must be triggered from pool-confirmed
  callback path, not from submit path).
- Type annotations completion in `miner.py` and `stratum.py` (return types and
  parameter types where missing).

**Docs**
- README gets a one-paragraph "What's new in v0.5.0".
- CHANGELOG entry for v0.5.0.
- `docs/handoff/pr-a-summary.md` — what shipped, file map, follow-on hooks.

**Tests must pass**: `py -3.11 -m unittest discover -s tests -v`.

### PR B — `feat/perf-and-resilience` (off PR A's branch)

Stdlib-friendly slice of Level 2.

**Features**
- Multi-pool failover. CLI: `--pool host:port` repeatable; if first pool fails to
  connect or stays disconnected for >30s, supervisor switches to next. Round-robin
  on cycle through all. New module `src/hope_hash/pools.py` (`PoolList`, rotation logic).
- Solo mode via `getblocktemplate`. `hope-hash --solo --rpc-url http://... --rpc-cookie path`
  fetches block template from a local bitcoind, builds the header, mines, on hit
  calls `submitblock`. JSON-RPC over `urllib`. New module `src/hope_hash/solo.py`.
  Authoritative reference: BIP-22/BIP-23. Coinbase build is the trickiest part:
  needs witness commitment (segwit) for any non-trivial template.
- Optional ctypes SHA-256 path. `src/hope_hash/sha_native.py` tries to load
  `libcrypto`/`libssl` via `ctypes.CDLL` (Win: `libcrypto-3.dll` / `libcrypto-1_1.dll`;
  Linux: `libcrypto.so.3`/`.1.1`; macOS: `/usr/lib/libcrypto.dylib`). Exposes
  `sha256_double(data: bytes) -> bytes`. Falls back to `hashlib` if load fails.
  CLI `--sha-backend {auto,hashlib,ctypes}` (auto = ctypes if available).
- Bench gets `--backends` flag — runs each available backend back-to-back for
  comparison.

**Quality**
- Tests for `pools.PoolList` rotation (skip-failed, wrap-around, single-pool no-op).
- Tests for `sha_native` (correctness vs hashlib on known vectors; fallback path).
- Tests for `solo.build_coinbase` (witness commitment, BIP141 path).
- Solo mode integration test uses a `FakeRPC` (no real bitcoind needed).

**Docs**
- CHANGELOG entry for v0.6.0.
- `docs/handoff/pr-b-summary.md`.

### PR C — `feat/web-and-docs` (off PR B's branch)

Web dashboard + Docker + full bilingual docs.

**Features**
- Web dashboard on stdlib `http.server` (extends `metrics.MetricsServer` or
  separate port via `--web-port`):
  - `GET /` — single-page HTML dashboard, vanilla JS, polls `/api/stats` every 2s.
  - `GET /api/stats` — JSON snapshot (hashrate, diff, workers, uptime, shares,
    current job_id, last share ts).
  - `GET /api/events` — Server-Sent Events stream; emits a line per share
    found/accepted/rejected and per job change.
- Docker:
  - `Dockerfile` (python:3.11-slim base, multi-stage if helpful).
  - `docker-compose.yml` with the miner + Prometheus + Grafana, volumes for
    SQLite and the dashboard JSON.
  - `.dockerignore`.

**Docs (the big one)**
- `README.md` rewritten: top half English, bottom half Russian. Both halves cover:
  what / install / run / advanced flags / demo / benchmark / architecture /
  realistic-expectations / contributing pointer.
- `docs/getting-started.en.md` and `docs/getting-started.ru.md` — step-by-step
  for a fresh user (Python install → BTC address → first run → reading logs).
- `docs/deploy.en.md` and `docs/deploy.ru.md` — Docker + Prometheus + Grafana +
  Telegram + healthcheck setup.
- `docs/architecture.en.md` and `docs/architecture.ru.md` — protocol, threading
  model, file map, hot path, observers, performance notes.
- CHANGELOG entry for v0.7.0.

## Subagent communication protocol

Each subagent:
1. Reads `docs/handoff/pr-{prev}-summary.md` (if present) to learn the deltas.
2. Branches off the previous PR's branch (or `main` for PR A).
3. Implements its scope. **Pure stdlib.** No `pip install`.
4. Runs full test suite; must pass.
5. Commits with conventional-commit messages, pushes, opens a draft PR via `gh`.
6. Writes `docs/handoff/pr-{this}-summary.md` with: file map, new flags, gotchas,
   open questions for next agent.
7. Returns a structured summary to the orchestrator (test count delta, files
   added/modified, PR URL).

## Final review panel (parallel)

After PR C is opened, four review subagents run **in parallel** against the full
diff (`origin/main..feat/web-and-docs`):

1. **code-reviewer** (`superpowers:code-reviewer`) — architecture, naming,
   adherence to `CLAUDE.md` invariants (endianness, hot path, error handling).
2. **security** — input validation surface (web dashboard, RPC cookie handling,
   Telegram command authz, ctypes loader path injection).
3. **docs/UX** — README EN/RU parity, getting-started accuracy on a fresh box,
   doc cross-links, dead links, copy quality.
4. **test-coverage** — coverage of new modules, missing edge cases, flaky tests,
   Windows/Linux/macOS specifics.

Each returns a markdown report. Orchestrator consolidates into
`docs/handoff/final-review.md` and surfaces top issues to the user.

## Out of scope

- Rust / PyO3 — needs toolchain.
- GPU (PyOpenCL/cupy) — needs deps.
- Stratum V2 — Noise protocol crypto in stdlib is a non-trivial sub-project.
- TUI on `rich` — not stdlib.
- FastAPI — not stdlib.
- k8s/Helm — over-engineering for this project size.
- Lottery visualization, shitcoin mining, pyinstaller bundling — fun ideas, not
  worth this push.

## Success criteria

- Three PRs open against `main`, stacked in order, each with green tests.
- README in EN+RU with copy-pasteable run instructions.
- Final review report identifies any blockers before merge.
- User can return, read the consolidated review, merge in order or request changes.
