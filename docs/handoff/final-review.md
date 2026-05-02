# Final review — three-PR stack (PRs #6, #7, #8)

**Date:** 2026-05-02
**Branch under review:** `feat/web-and-docs` (tip: `021ffa1`)
**Stack:** PR #6 → #7 → #8, all draft, stacked off `main`.
**Tests:** 242 passing, ~17s wall, no flakes.
**Reviewers:** code, security, docs/UX, test coverage (4 parallel agents).

Detailed reports:
- [`review-code.md`](./review-code.md)
- [`review-security.md`](./review-security.md)
- [`review-docs.md`](./review-docs.md)
- [`review-tests.md`](./review-tests.md)

---

## Verdict

**Mergeable after one BLOCKER fix and four small SHOULD-FIX items.**
Stack is well-structured, stdlib-only, hot path untouched, observers cleanly
decoupled, bilingual docs are real (not machine-translated), zero HIGH security
findings, 242 tests passing.

The one blocking bug is in `solo.py` prev-hash byte-order — masked by the
all-zero `FAKE_TEMPLATE` prevhash in `test_solo.py`, so against a real
`bitcoind` solo mode would silently mine invalid blocks.

## Blocker (fix before merge)

### B1 — `solo.py:514-521` prev-hash word-swap is a no-op

`_template_to_job` builds `prev_stratum_hex` such that `swap_words` in
`_build_header_base` cancels out, leaving the mined header with `prev_be`
display order instead of internal little-endian `prev_be[::-1]`. Submit-time
serializer in `_assemble_header` independently does the right reversal, so
hashing-time and submit-time headers disagree.

**Hidden by:** `FAKE_TEMPLATE.previousblockhash = "0" * 64` (fixed point under
any byte permutation). No real-bitcoind integration test catches it.

**Fix sketch** (from code-reviewer):
```python
internal = bytes.fromhex(tmpl["previousblockhash"])[::-1]
prev_stratum_hex = b"".join(internal[i:i+4][::-1]
                            for i in range(0, 32, 4)).hex()
```

**Test gap to close together:** add a `test_solo.py` case with a real
non-symmetric mainnet block hash and assert `_build_header_base` produces
exactly `prev_display[::-1]`.

## Should-fix (recommend before merge, all small)

### S1 — `docker-compose.yml:10` comment misnames web-dashboard port
Comment points at `:8000` (which is `/metrics`+`/healthz`); web dashboard is
on `:8001`. First-run users hit the Prometheus exposition page. (docs review)

### S2 — `docker run` example in `deploy.{en,ru}.md` §6 has confusing positional
The literal `docker` is the worker_name positional, but reads as a subcommand.
Rename to `mybox` and add a comment. (docs review)

### S3 — `CHANGELOG.md` link footer stale
v0.3.0–v0.7.0 are unlinked; `[Unreleased]` still compares against v0.2.0.
Markdown silently broken. (docs review)

### S4 — README advanced-flags table omits `--log-file`
Flag is real (`cli.py:108`), used in TUI workflow. Missing in EN and RU
halves. (docs review)

## Security MEDIUMs (not blocking the documented use-case, fix before any non-localhost deploy)

### M-1 — `/api/events` SSE has no concurrent-connection cap
Per-connection `queue.Queue(maxsize=256)` + thread; `ThreadingHTTPServer`
spawns one thread per request; loopback default mitigates, but
`docker-compose.yml` flips bind to `0.0.0.0`. **Fix:** `max_subscribers` cap
+ HTTP 503 on overflow.

### M-2 — `BitcoinRPC` cookie file read is unbounded and unguarded
`Path(cookie_path).read_text()` with no size cap and no `try/except`.
Footgun: `--rpc-cookie /tmp` OOMs the miner. **Fix:** stat → size limit 4 KiB
→ `OSError` → friendly CLI error.

### M-3 — ctypes loads `libcrypto-3.dll` by bare name on Windows
DLL search order includes app dir → writable cwd hijack possible. **Fix:**
`ctypes.WinDLL(..., winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)` or
`os.add_dll_directory()` with a known-good path.

### M-4 — Docker compose binds to `0.0.0.0` + ships Grafana `admin/admin`
`ports: "8001:8001"` and `"3000:3000"` publish broadly; Docker bypasses
host firewall via `iptables`. **Fix:** prefix with `127.0.0.1:`; make
`GRAFANA_PASSWORD` mandatory via `${GRAFANA_PASSWORD:?...}` idiom.

## Test gaps (no blockers, but high-value to add next iteration)

1. **Mid-state ↔ ctypes parity sentinel** — guards against silent endianness
   regression in `_worker_ctypes` vs `_worker_hashlib_midstate`. Hardware-
   independent, cheap.
2. **`SoloClient.reader_loop` RPC failure path** — uncovered.
3. **`SoloClient` without `default_witness_commitment`** (regtest/non-segwit
   templates).
4. **`cli._build_pool_list` and `_resolve_sha_backend`** — pure helpers, zero
   tests today. Add `tests/test_cli_helpers.py`.
5. **`webui._serve_events` cleanup on subscriber drop** — verify
   `_subscribers` list returns to baseline length after disconnect.
6. **`StatsProvider.publish_event` under concurrent subscribe/unsubscribe** —
   intentional design (snapshot-then-call) deserves a smoke test.

## Non-blocking code concerns

- `cli.py:460-466` — `stats_provider.update_hashrate` monkey-patch. Lift
  `last_hashrate_ts` into `StatsSnapshot` and drop the wrapper. Already flagged
  in PR-A handoff as an open question.
- `notifier.py:307` — dead boolean clause; intent is probably
  `if item is None or self._stop_event.is_set():`.
- `solo.py:489-499` — `deadbeef` extranonce marker has 2⁻³² collision risk.
  Use 16-byte `os.urandom`.
- `solo.py:407-452` — `submit()` runs synchronously in mine thread; OK for
  learning code, document in `architecture.{en,ru}.md`.
- `webui.py:331-336` — `repr(exc)` leaks into healthz HTTP body; loopback
  default mitigates.
- `parallel.py:275-276` — pre-existing bare `except Exception: pass` on queue
  cleanup. Out of stack scope.

## Docs nice-to-haves

- N1: Validate `--solo` argument tuple before BTC address validation.
- N2: argparse help text is Russian-only; bilingual everywhere else.
- N3: `architecture.{en,ru}.md` doesn't note that telegram inbound requires
  `HOPE_HASH_TELEGRAM_INBOUND=1` opt-in.
- N6/N7: `deploy.{en,ru}.md` §3 incorrectly says "503 otherwise" — actual
  is 200 for `degraded`, 503 only for `down`.
- N10: `Dockerfile` `EXPOSE` lists 8000+9090 but compose uses 8000+8001.
- N11: `architecture.{en,ru}.md` file-map omits `_logging.py`/`__main__.py`.

## Praise (worth keeping in mind for future agents)

- **`StatsProvider` as canonical bus** — clean decoupling of mine() from TUI,
  web, healthz, Prometheus. Pub/sub added in PR C without touching call sites.
- **`PoolList`** — `mark_failed` returns rotation flag, `full_cycle_failed`
  distinguishes flap from outage, `RLock` prevents the obvious deadlock and
  there's a test pinning the invariant.
- **ctypes loader hygiene** — `c_void_p` restypes (catches 64-bit truncation),
  `EVP_sha256` symbol probe (catches wrong DLL), `try/finally` around
  `EVP_MD_CTX_free` (no leak/UAF).
- **`render_html` is fully static** — no user input echoed in, no CDN, no
  external scripts. XSS-safe by construction.
- **`test_notifier_timing.py`** — exemplary regression sentinel for the
  submit-vs-ack semantic that already bit the project once.
- **`test_solo.py` (38 tests)** — well-spent on the most error-prone module;
  byte-level assertions on `_varint`, `_push_data`, `_serialize_height`,
  witness commitment parse/compute.
- **Bilingual docs** — EN/RU mirror line-for-line; Russian reads as
  written-by-a-human, technical terms stay English by convention.
- **Defaults are conservative** — `--web-host 127.0.0.1`, `--web-port 0`,
  `HOPE_HASH_TELEGRAM_INBOUND=0` — exactly the right posture.

## Recommended merge order

1. Land **B1** fix + the matching prev-hash test against PR #7 branch.
2. (Optional now / can defer) land **S1–S4** doc/comment fixes against PR #8
   branch.
3. (Defer to a follow-up PR) **M-1–M-4** security MEDIUMs and the six
   test-coverage additions.
4. Merge PR #6 → main, then rebase #7 onto main + merge, then rebase #8 onto
   main + merge.

Total fix budget for blocking + should-fix: ~30 minutes of engineering work
plus tests. The four MEDIUMs and the test gaps are a clean follow-up PR.
