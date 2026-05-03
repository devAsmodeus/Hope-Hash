# Test coverage & quality review

Branch: `feat/web-and-docs` (PRs A → B → C, v0.5.0 → v0.7.0).
Test count claim: **242 OK** — verified locally `Ran 242 tests in 17.4s — OK`,
`countTestCases() == 242`.

## TL;DR

The suite is in solid shape: 242 passing, ~17s wall, no flakes seen on a clean
Windows 11 + Python 3.11 run. New modules from PRs A/B/C all have dedicated
`test_<module>.py` files with happy-path + main error-path coverage. Largest
gaps are in **integration glue** (CLI helpers `_resolve_sha_backend` /
`_build_pool_list`, mid-state↔ctypes parity, SoloClient long-poll on RPC
failure) and **stress** (no concurrent-writer race tests on `StatsProvider`
publish path; SSE multi-subscriber). No BLOCKER-level gap; a handful of
SHOULD-FIX items.

## Test count summary (file → tests)

| File | Tests |
| --- | --- |
| test_address.py | 18 |
| test_banner.py | 6 |
| test_bench.py | 3 |
| test_bench_backends.py | 8 |
| test_block.py | 21 |
| test_demo.py | 1 |
| test_healthz.py | 12 |
| test_metrics.py | 16 |
| test_notifier.py | 23 |
| test_notifier_timing.py | 5 |
| test_pools.py | 22 |
| test_sha_native.py | 12 |
| test_solo.py | 38 |
| test_storage.py | 11 |
| test_stratum.py | 15 |
| test_tui.py | 14 |
| test_webui.py | 17 |
| **Total** | **242** |

Module → test mapping is 1:1 for every new module (`tui`, `banner`, `pools`,
`sha_native`, `solo`, `webui`, `bench_backends`, `notifier_timing`, `healthz`).

## Blockers (must add before merge)

None. All load-bearing code paths have at least basic coverage and the suite
runs green deterministically.

## Should-fix (next iteration)

1. **Mid-state vs ctypes hash parity sentinel.** `parallel.py` dispatches to
   `_worker_hashlib_midstate` or `_worker_ctypes` based on `sha_backend`. There
   is no test asserting that for a given `(header_base, nonce)` both code
   paths produce the same digest. A regression here (e.g., wrong endianness in
   one path) would silently halve hashrate or corrupt found shares.
   Add to `tests/test_sha_native.py` or new `tests/test_parallel_parity.py`:
   compute `block.double_sha256(header_base + nonce_le)` and compare against
   `sha_native.sha256d(header_base + nonce_le)` for ~10 random nonces.

2. **`solo.SoloClient.reader_loop` RPC failure path.** Loop catches
   `URLError/RPCError/OSError`, logs, sets `sock = None`, and returns. No test
   covers this. `tests/test_solo.py` has FakeRPC for happy path only.
   Add: a `FakeRPC` whose `call("getblocktemplate", ...)` raises `RPCError`,
   spin `reader_loop` in a thread, assert it returns within ~1s and `sock` is
   `None`.

3. **`SoloClient` without `default_witness_commitment` (non-segwit/regtest).**
   `_build_coinbase_for_template` calls `tmpl.get("default_witness_commitment")`
   and tolerates missing key, but no test asserts the resulting coinbase has
   exactly one output. Add a FAKE template with that key removed and verify
   `len(build_coinbase(...))` matches the no-witness branch.

4. **`pools._build_pool_list` and `_resolve_sha_backend` (cli.py:202, 215).**
   Both are CLI helpers with branching logic — neither has any test. Even if
   we don't test `main()`, these two functions are pure (take args, return
   value). Add a `tests/test_cli_helpers.py` covering: default fallback to
   `(POOL_HOST, POOL_PORT)` when `--pool` empty, multiple `--pool` flags
   parsed in order, `auto` resolves to ctypes iff available, explicit `hashlib`
   passes through unchanged.

5. **`webui._serve_events` cleanup on subscriber drop.** `test_webui.py`
   verifies that one event reaches the client, but never asserts that
   `provider._subscribers` returns to the original length after the SSE
   client disconnects. Add: subscribe count = N; open SSE; close socket;
   wait short; assert subscribe count back to N.

6. **`MetricsServer` concurrent `start()/stop()`.** Both lock-protected, no
   test. Spawn 4 threads each calling `start()`+`stop()`; assert no port-bind
   exception, no leaked thread.

7. **`StatsProvider.publish_event` under concurrent subscriber churn.**
   `test_thread_safety_smoke` only writes/reads snapshot. We don't have a test
   where one thread `subscribe`/`unsubscribe`s while another publishes — the
   intentional design (snapshot list under `_sub_lock`, then call without
   lock) deserves a smoke test.

8. **`bench --backends` only-hashlib path.** When ctypes is unavailable, the
   final summary line is `[bench] result: hashlib-midstate ... (ctypes
   недоступен)`. There is no explicit assertion of that message (or shape of
   `results` dict with single key) — a regression in the summary block would
   slip through.

9. **`notifier._fetch_updates` 502/HTTPError path.** `_inbound_loop` catches
   `URLError`, but `HTTPError` (subclass of URLError) on Telegram outage isn't
   exercised. Add a mocked `urlopen` that raises `HTTPError` and assert the
   loop survives one cycle and backs off.

10. **`webui.WebUIServer` host/port already in use.** `start()` will raise
    `OSError` from `ThreadingHTTPServer((host, port), ...)`. No test asserts
    behavior. Either document that `start()` propagates, or wrap and skip in
    test with `unittest.skipIf` on busy port.

## Nice-to-have

- **`sha_native.sha256d` empty bytes & 1MB input.** Empty is covered for
  `sha256` but not explicitly for `sha256d`. Large input (~1MB) would catch
  any `c_size_t` truncation — current sigs use `c_size_t` so risk is low,
  but cheap to add.
- **`pools.parse_pool_spec` with IPv6 literal `[::1]:3333`.** Current
  `rpartition(":")` does the wrong thing on IPv6. Decision needed:
  document as unsupported, or handle `[..]:port`.
- **`PoolList` with one pool that always fails.** Implicitly covered by
  `test_single_pool_rotates_to_self` + `test_full_cycle_failed_after_all_rotations`,
  but a combined "1 pool, threshold=1, mark_failed loop 100x → still
  consistent" smoke test would document the no-deadlock invariant.
- **`tui.format_rate` boundary at exactly 1000 H/s and 1_000_000 H/s.** Off-by-one
  bracketing isn't checked; trivial assertion.
- **`MetricsServer.set_health_provider(None)` after a real provider was
  registered.** Docstring says supported; not tested.
- **`webui` HTML CSP smoke.** Optional: assert the page does not contain
  `<script src=` (already done) AND no `eval(`/`new Function(`. Documents
  the inline-only contract.
- **`solo.compute_merkle_root_from_txids` parity with `block.build_merkle_root`**
  for a non-trivial transaction list — would catch divergence between the two
  merkle implementations.
- **`bench.BenchResult` JSON-serializability.** `dataclass` is fine but a
  one-liner `json.dumps(asdict(r))` would lock in the schema for future
  tooling.

## Cross-platform notes

- Windows-specific paths handled well:
  - `sha_native._CANDIDATES_WIN` covered by parity tests when ctypes loads,
    otherwise gracefully reported via `BACKEND_NAME == "hashlib-fallback"`.
  - `tui.is_curses_available()` returns `bool` test, doesn't assert value —
    correct for cross-platform CI.
  - `test_demo.py` and `test_bench.py` use `mp.get_context("spawn")`
    explicitly — the right call for Windows-correct multiprocessing tests.
- Linux/macOS specifics:
  - `sha_native._CANDIDATES_LINUX` / `_CANDIDATES_MACOS` rely on real
    libcrypto presence; no isolated test mocks `_try_load_libcrypto`. A test
    with `@patch("hope_hash.sha_native._try_load_libcrypto")` returning
    `(None, "")` and reloading the module via `importlib.reload` would
    deterministically exercise the fallback branch on any platform.
- Port-reuse hazard: `tests/test_webui.py::_free_port` and
  `tests/test_healthz.py::_free_port` both use the bind-then-close trick.
  PR A summary acknowledges the race; on a busy CI matrix this could flake.
  Recommend adding a `@retry_on_port_in_use(n=3)` decorator if it ever
  materializes.
- Windows-curses: gracefully degrades; no test attempts to render the curses
  loop (correct — curses needs a TTY).

## Performance regression sentinels

None present. `bench.py` is exercised but with 0.5s–1.0s durations purely
for "did it produce >0 hashes?" — by design no hashrate threshold (would be
flaky across machines). Recommended sentinel: a **correctness** sentinel
(see Should-fix #1) that proves `_worker_ctypes` and `_worker_hashlib_midstate`
emit identical digests for the same input. This is hardware-independent and
would catch a class of "silently produced wrong shares" regressions that no
hashrate number can detect.

## Praise

- **`test_notifier_timing.py`** is exemplary: it nails a real correctness
  invariant (`notify_share_accepted` only on pool ack, never on submit) with
  cheap fake-socket fakes — no network, no sleeps, no flakes. Good template
  for future timing-sensitive tests.
- **`test_pools.py`** covers the deadlock-on-RLock invariant explicitly
  (`test_nested_calls_dont_deadlock`) — the kind of test that catches
  refactors that "looks right" but breaks the lock contract.
- **`test_solo.py`** covers all five low-level helpers (`_varint`,
  `_push_data`, `_serialize_height`, witness commitment parse/compute,
  merkle root) with explicit byte-level assertions, plus a FakeRPC
  integration. 38 tests on the most error-prone module is well-spent.
- **`test_webui.py::test_sse_receives_published_event`** does the right
  thing: raw socket, manual HTTP/1.1, reads with timeout — the only reliable
  way to test SSE without an HTTP client that wants to read the full body.
- **`test_healthz.py::TestBuildHealthSnapshot`** uses `now=...` injection
  for deterministic time tests instead of `time.sleep` or `freezegun`. Best
  practice for time-dependent logic.
- **`StatsProvider.snapshot()` returns a copy** (verified by
  `test_snapshot_is_a_copy`) — the type of invariant that's invisible until
  it breaks; nice to see it pinned.
