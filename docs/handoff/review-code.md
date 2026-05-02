# Code review — three-PR stack (PRs #6, #7, #8)

## Summary verdict

**Minor fixes needed.** The stack is well structured, observes every CLAUDE.md
invariant I checked (no new pip deps, hot path untouched, endianness rules
respected in `block.py`/`miner._build_header_base`, Russian comments,
`[tag]` log convention), and all 242 tests pass cleanly. There is **one
load-bearing correctness bug in `solo.py` prev-hash conversion** that the
unit tests miss because the FAKE_TEMPLATE uses an all-zero prevhash; against
a real `bitcoind` solo mode would mine the wrong header. Several
non-blocking smells around the "monkey-patch update_hashrate" hack in
`cli.main`, an inconsistent docker-compose comment, and a couple of looser
exception clauses.

## Blocking issues

### 1. `solo.py:514-521` — prev-hash word-swap is a no-op, header gets wrong prev hash

`_template_to_job` constructs `prev_stratum_hex` by reversing bytes inside
each 4-byte word but **leaving word order untouched**. Then
`_build_header_base` (miner.py:158) calls `swap_words(job["prevhash"])` which
also reverses bytes inside each 4-byte word. Two byte-within-word reversals
compose to identity, so the prev hash placed in the mined header equals
`prev_be` (display BE) instead of the required internal little-endian
`prev_be[::-1]`.

Concrete trace with `prev_display = 01 02 03 04 05 06 07 08`:
- header needs `08 07 06 05 04 03 02 01` (full byte reversal)
- code produces `01 02 03 04 05 06 07 08` after both transforms
- `_assemble_header` (solo.py:627) at submit time independently does
  `prev_le = prev_be[::-1]` which is *correct*, so the header at hashing
  time and the header at submit time disagree — even if mining hit the
  target on the wrong header, the submitted block would be invalid
  (wrong hash vs. target).

The unit tests pass only because `FAKE_TEMPLATE.previousblockhash = "0" * 64`
is a fixed point under any byte permutation. Add a test with a non-symmetric
prevhash (e.g. take a real mainnet block hash) and `_build_header_base`
should produce `prev_display[::-1]` exactly.

Fix sketch: build `prev_stratum_hex` as
`swap_words_hex(prev_display[::-1].hex())`, i.e. take the desired internal
ordering and apply `swap_words` (which is its own inverse) so that
`_build_header_base`'s `swap_words(...)` cancels back to the internal
ordering. Equivalently:
```python
internal = bytes.fromhex(tmpl["previousblockhash"])[::-1]
prev_stratum_hex = b"".join(internal[i:i+4][::-1]
                            for i in range(0, 32, 4)).hex()
```

This is the core load-bearing piece of solo mode. Without the fix, every
solo deployment against a real bitcoind silently mines invalid blocks.

## Non-blocking concerns

### 2. `docker-compose.yml:10` — comment misnames the web dashboard port

The header banner says `open http://localhost:8000 # web-дашборд`, but the
service exposes `8000` for `/metrics`+`/healthz` and `8001` for the web
dashboard (cf. `command:` lines 32-33 and `ports:` 47-48; matches
`docs/deploy.{en,ru}.md:34`). Newcomers following the comment will hit a
404. Swap the URL to `:8001` and add a `:8000` line for metrics.

### 3. `cli.py:460-466` — `stats_provider.update_hashrate` is monkey-patched on a live instance

```python
stats_provider.update_hashrate = _wrapped_update  # type: ignore[method-assign]
```
Comment at PR-A handoff (`docs/handoff/pr-a-summary.md:96-100`) already
flags this as an open question. Two reasons to clean it up:
1. Method assignment on a dataclass-like provider is brittle — a future
   refactor that converts `StatsProvider` to a `Protocol` or adds slots
   would silently break.
2. The same timestamp could live as `last_hashrate_ts` on `StatsSnapshot`
   (set inside `update_hashrate`) and read directly by the health provider —
   keeps the data flow in one place.

Recommend lifting `last_hashrate_ts` into `StatsProvider`/`StatsSnapshot`
and dropping the wrapper. Touches CLI + tui only.

### 4. `solo.py:396-400` — RPC failure returns silently from the reader loop

```python
except (urllib.error.URLError, RPCError, OSError, json.JSONDecodeError) as e:
    logger.warning("[solo] getblocktemplate failed: %s", e)
    self.sock = None
    return
```

That kills the reader, which is fine — supervisor will restart. But
returning on the first transient failure (network hiccup, bitcoind
restart) means every flap costs a full reconnect cycle. PR-B summary
already calls this out as a known tradeoff. Worth a single-retry with
short backoff before giving up; not blocking.

### 5. `notifier.py:307` — sentinel comparison has accidental boolean precedence

```python
if item is None or self._stop_event.is_set() and item is None:
```

Python parses this as `item is None or (self._stop_event.is_set() and item is None)`
which is logically equivalent to just `item is None`. The second clause
is dead code. Either drop it or write
`if item is None or self._stop_event.is_set():` if the intent is "exit
sentinel OR explicit stop request". Worker would then not call `_send` on
a residual real message after `stop_event` is set — probably the intent.

### 6. `cli.py:308-310` — solo CLI message lists wrong env var convention

The error string says `--rpc-cookie ИЛИ --rpc-user/--rpc-pass` (Russian),
but earlier auth-validation messages and the rest of `--help` text mostly
use English-only error prose. Sample existing flow (`cli.py:266, 291`)
uses Russian too, so this is consistent — leave as-is, but worth one pass
for tone before tagging v1.0.

### 7. `webui.py:331-336` — broad `except Exception` on health-provider call

```python
except Exception as exc:  # noqa: BLE001 — пользовательский callable
    payload = {"status": "down", "reason": f"provider error: {exc}"}
    http_status = 503
else:
    status = payload.get("status", "down")
    http_status = 503 if status == "down" else 200
```

Catching `Exception` here is justified (third-party callable), and the
`# noqa` is honest. But the formatted reason leaks `repr(exc)` to the
HTTP body — fine for an internal `/healthz` on loopback, surprise on a
public endpoint. PR-C summary already notes web-host=127.0.0.1 default;
worth a one-liner in `docs/deploy.*.md` reminding ops not to expose
`/healthz` raw.

### 8. `solo.py:407-452` — `submit()` runs `submitblock` synchronously inside the producer thread

`mine()` calls `client.submit(...)` directly from the main mine thread
when a share is found. With Stratum that's a one-line socket write; with
solo it's a full `submitblock` HTTP round-trip plus block serialization
(coinbase rebuild + merkle re-roll). On a busy template that's tens to
hundreds of milliseconds with main-thread blocked. For a learning code
path where finds are ~10⁻¹⁵/day, this is fine; flag it in
`docs/architecture.*.md` so it doesn't get refactored away by mistake.

### 9. `parallel.py:275-276` — bare `except Exception: pass` on queue cleanup

```python
try:
    found_queue.close()
    found_queue.join_thread()
except Exception:
    pass
```

Pre-existing (not part of this stack), but worth narrowing to
`(OSError, AssertionError)` per CLAUDE.md `Patterns to avoid` rule about
bare except. Not in scope for these PRs.

### 10. `solo.py:489-499` — extranonce marker uses a plain string substring search

`coinbase_skel_marked = self._build_coinbase_for_template(tmpl, "deadbeef")`
then `find(b"\xde\xad\xbe\xef")`. The marker could collide with bytes
already present in the template (height encoding, witness commitment hash
that happens to start with the magic). For the fixed marker
`deadbeef`, the chance is ~2⁻³² per build — practically never, but if it
happens once in production the user gets a `RuntimeError`. Use a longer
marker (16 bytes of `os.urandom` per call) and strip it out — same
performance, zero collision risk.

## Praise

- **`StatsProvider` as the canonical bus is the right call.** It cleanly
  decouples mine() from TUI, web, healthz, and Prometheus. Adding the
  pub/sub layer in PR C without churn to existing call-sites is exactly
  the layered design the spec asked for.
- **`PoolList` semantics are well-thought.** `mark_failed` returns
  whether it rotated, `full_cycle_failed` distinguishes "one pool flapping"
  from "internet down", `RLock` prevents the obvious self-deadlock in
  `mark_failed → _rotate_locked`. Tests cover the deadlock case
  explicitly (`test_pools.py:135`).
- **`set_endpoint` on `StratumClient` preserves callbacks and locks.**
  Avoiding object recreation is what lets the upstream `mine()` keep its
  `on_share_result` registration alive across pool rotations — exactly
  the gotcha PR A flagged for PR B.
- **ctypes loader is defensive.** Tries platform-specific candidates,
  then `ctypes.util.find_library` as fallback, validates by probing for
  `EVP_sha256` symbol presence (catches loading the wrong dll). `c_void_p`
  restypes for the pointer-returning EVP functions — without that this
  would silently truncate on 64-bit and crash mysteriously.
- **Documentation parity is real.** Skimming `docs/deploy.{en,ru}.md`
  and `docs/architecture.{en,ru}.md` showed mirrored sections, mirrored
  code blocks, no machine-translation smell.
- **CHANGELOG entries actually describe the diff.** v0.7.0 section reads
  like a release note, not a commit log dump.
- **Test discipline:** `test_notifier_timing.py` is exactly the regress-
  test class that prevents silent regressions when someone tries to
  "simplify" `mine()` and accidentally moves `notify_share_accepted` out
  of the ack callback. The 5 cases (no-notify-on-submit, notify-on-ack,
  no-notify-on-reject, exactly-once-on-duplicate, once-per-distinct)
  cover the matrix.

## Per-PR notes

### PR #6 (`feat/ops-and-ux`, v0.5.0)

Clean, well-bounded. `tui.py` correctly hides curses behind
`is_curses_available()` so Windows-without-windows-curses degrades
gracefully. `banner.py` is simple and tested. `notifier.py` inbound is
the riskiest piece and it gets the basics right: chat_id authz before
dispatch, daemon thread, command registry under lock, exception isolation
in handlers. The `_handle_update` offset-tracking is correct (only
advance on incoming `update_id`).

Concerns: see #3 (monkey-patch) and #5 (sentinel logic).

### PR #7 (`feat/perf-and-resilience`, v0.6.0)

`pools.py` is excellent. `sha_native.py` ctypes loader is professional.
`solo.py` is the largest module in the stack and most of it is right —
`build_coinbase`, `compute_witness_commitment`, `parse_default_witness_commitment`,
`compute_merkle_root_from_txids`, `serialize_block`, varint, push_data,
height encoding all check out against the BIP texts and have good unit
coverage. The merkle branch construction in `_merkle_branch_from_txids`
is subtle but correct (verified with 1/2/3-tx traces).

The blocking issue is **#1 prev-hash byte order** — caught only because
I traced through with a non-symmetric prev hash. This is a real bug for
real bitcoind users.

`parallel.py` worker dispatch is clean: hot-path `_worker_hashlib_midstate`
is byte-for-byte unchanged from v0.4.0; the new `_worker_ctypes` is a
separate function so the hot path can't accidentally regress.

### PR #8 (`feat/web-and-docs`, v0.7.0)

`webui.py` SSE handler is correctly structured: `subscribe()` returns
unsubscribe, `try/finally` guarantees cleanup, `BrokenPipe`/
`ConnectionReset`/`OSError` caught explicitly on writes, queue with
`put_nowait` + warn-and-drop avoids blocking the publisher. Keepalive
every 15s with `X-Accel-Buffering: no` for nginx — the right defaults.
`protocol_version = "HTTP/1.1"` is required for SSE chunked transfer
and is correctly set.

`render_html()` is one chunk of static HTML with inline CSS and JS, no
external CDN, no font CDN, no script-src — defends against the obvious
supply-chain risk on a dashboard the user might expose. ✓

`Dockerfile` healthcheck uses stdlib `urllib` (no `curl` install
overhead), pip-installs the project itself (which the CLAUDE.md `pip
install ...` rule explicitly allows). `docker-compose.yml` is mostly
solid; see #2 for the comment fix.

Documentation: README EN and RU mirror cleanly; section headers, table
columns, advanced-flag rows all align. `docs/*.{en,ru}.md` line counts
within ~5% of each other (109/108, 142/139, 97/94) — that's the right
parity signal. No dead links spotted in spot-checks.
