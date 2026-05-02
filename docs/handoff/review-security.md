# Security review — three-PR stack

Scope: PR #6 `feat/ops-and-ux`, PR #7 `feat/perf-and-resilience`,
PR #8 `feat/web-and-docs` reviewed against `origin/main`. Threat model
assumed: operator runs on their own box; no hostile pool; bitcoind is
trusted; the only adversaries that matter are network-reachable parties
on the LAN where the operator chose to expose ports.

## TL;DR

Zero HIGH issues. The new attack surface (`webui.py`, Telegram inbound,
ctypes loader, solo RPC, multi-pool parser, Docker stack) is conservative
by default — loopback bind, opt-in inbound, cookie-first auth, no
shell/eval/pickle, and HTML is a fully static template (no user input
echoed into it). 4 MEDIUM findings worth fixing before merge: (1) `/api/events`
has no per-host SSE-connection cap and will spin up unbounded queues +
threads under load; (2) `BitcoinRPC` reads the cookie file with no size
limit and no error-handling around the read; (3) `ctypes` library load is
done by short name only — Windows DLL search-order can be hijacked when
the miner is launched from a writable cwd; (4) the `docker-compose` stack
publishes ports `0.0.0.0:8001` (web) and `3000` (Grafana) by default
while Grafana ships with `admin/admin` — fine for `localhost`-only
docker hosts, dangerous on a public VPS. The stack is mergeable as-is for
the stated learning use-case; the four MEDIUM items are easy follow-ups.

## Findings

### HIGH

None.

### MEDIUM

#### M-1 — Unbounded SSE connections leak threads, queues, and FDs

`src/hope_hash/webui.py:344-400` — `_serve_events` calls
`provider.subscribe(_on_event)` and allocates a `queue.Queue(maxsize=256)`
per connection. `WebUIServer` uses `ThreadingHTTPServer`, which spawns one
thread per request with no concurrency cap. The `unsubscribe()` runs in
`finally`, but only when the loop exits — for a slow/idle attacker with
keep-alive, the loop blocks on `ev_queue.get(timeout=1.0)` forever and
keepalive writes succeed because the kernel buffer never fills. An
attacker who can reach `--web-host` (loopback by default, but
`docker-compose.yml:34` flips it to `0.0.0.0`) can open thousands of
parallel SSE connections, each allocating a 256-slot queue and a thread.
Memory growth ~few MB per 1k connections, plus per-conn FDs (kernel
ulimit will eventually bite).

Fix: cap concurrent SSE subscribers (e.g. reject with HTTP 503 once
`len(provider._subscribers) > N`, default N=16). Lower-effort variant:
add `max_connections` knob on `WebUIServer` and a counter under a lock.
Document that the dashboard is single-user.

Severity rationale: requires network reach to the web port. With the
loopback default it's only a local-attacker concern; with the compose
default (`0.0.0.0`) it's anyone on the LAN.

#### M-2 — `BitcoinRPC` cookie file read is unbounded and unguarded

`src/hope_hash/solo.py:274-280`:
```python
if cookie_path is not None:
    cred = Path(cookie_path).read_text(encoding="utf-8").strip()
    self._auth = base64.b64encode(cred.encode()).decode()
```

`Path.read_text()` will load whatever the user passes — a 4 GB file at
the cookie path would OOM the miner before it ever reaches the bitcoind.
A real bitcoind cookie is ~80 bytes (`__cookie__:<hex>`). There's also
no `try/except` around the read: a missing or unreadable cookie raises
`FileNotFoundError`/`PermissionError` straight to `cli.main()`, which
prints a Python traceback rather than the friendly CLI error the rest of
the file uses.

Fix: stat the file first, refuse if `>4 KiB`, catch `OSError` and emit a
clean "error: failed to read --rpc-cookie {path}: {reason}" before
`sys.exit(2)`.

Severity rationale: operator-controlled path, so not exploitable from
network. Still a footgun and makes `--rpc-cookie /tmp` a hard hang.

#### M-3 — ctypes loads `libcrypto-3.dll` by bare name (Windows DLL search-order hijack)

`src/hope_hash/sha_native.py:45-92` — on Windows, `ctypes.CDLL("libcrypto-3.dll")`
goes through the standard DLL search order, which on default Windows 10/11
configurations includes the application directory (the directory of
`python.exe` for `hope-hash`) before `System32`. If a user installs
hope-hash globally (`pip install -e .` into a system Python) and then runs
`hope-hash bc1q...` from a *writable, attacker-influenced* cwd that contains
a hostile `libcrypto-3.dll`, the DLL gets loaded and its `EVP_*` exports
execute in-process. The `hasattr(lib, "EVP_sha256")` check after load is
not a defence — by then DllMain has already run.

Same risk theoretically exists on Linux if `LD_LIBRARY_PATH` is attacker-
influenced, but that's a much harder precondition.

Fix options (any one is enough):
- On Windows, prepend `os.add_dll_directory(...)` for a known-good path
  and never call bare `CDLL("libcrypto-3.dll")`.
- Use `ctypes.WinDLL("libcrypto-3.dll", winmode=0x00000800)` (i.e.
  `LOAD_LIBRARY_SEARCH_SYSTEM32`).
- Skip the ctypes path on Windows entirely (the stdlib `hashlib` path is
  already the default winner).

Severity rationale: requires (a) Windows, (b) install + run from a
writable working directory, (c) attacker write access there. Realistic
for somebody who downloads the project into Downloads and double-clicks.
Not realistic in the documented Docker deployment.

#### M-4 — Docker compose exposes ports broadly + Grafana ships `admin/admin`

`docker-compose.yml:46-48, 73-83` — `ports:` mappings publish
`8001:8001` (web dashboard) and `3000:3000` (Grafana) on `0.0.0.0` by
default. The Docker daemon's `iptables` rule bypasses host firewalls.
Combined with `GF_SECURITY_ADMIN_USER=admin` / `GF_SECURITY_ADMIN_PASSWORD=admin`
defaults from the same compose, anyone reachable on the LAN can land on
the Grafana login page with the documented creds. The web dashboard at
`8001` is read-only (no POST routes, verified — the only `do_GET` accepts
4 paths and otherwise returns 404), so leak risk there is just the BTC
address visible in logs and current job_id. Grafana is the bigger hole —
default admin gives full datasource & user-mgmt control on that
container.

`docs/deploy.en.md:36-38` and the RU mirror do mention `GRAFANA_PASSWORD`,
which is correct, but a stronger note ("MUST set on any non-laptop
deploy") would lower the chance of someone copy-pasting the stack onto a
public VPS.

Fix:
- Bind compose ports to `127.0.0.1:` by default (e.g.
  `"127.0.0.1:8001:8001"` and `"127.0.0.1:3000:3000"`); document the
  reverse-proxy pattern for external exposure.
- Make `GRAFANA_PASSWORD` mandatory: `${GRAFANA_PASSWORD:?set GRAFANA_PASSWORD}`
  same idiom already used for `BTC_ADDRESS`.
- Add a `USER` directive to `Dockerfile` (currently runs as root). Not
  blocking — pure-Python miner, no privilege ops — but free hardening.

Severity rationale: zero-config deploy on a host with public IP is the
most likely sharp edge. `localhost`-only deploy is fine.

### LOW

#### L-1 — `parse_pool_spec` has no host validation (correctness, not security)

`src/hope_hash/pools.py:152-174` — accepts any non-empty string as
`host`. No newline rejection, no IPv6 bracket handling (`[::1]:3333`
parses as `host="[::1]"`, port=3333 — surprisingly correct because
`rpartition(":")` keeps the bracket together with the prefix). Stratum
host is later passed straight to `socket.create_connection((host, port))`,
which itself validates. A stray `\n` in the host would manifest as
`getaddrinfo` failure, not as protocol injection — there's no Stratum
text channel where a hostile host string would matter.

No fix required for the stated threat model. If you want to be tidy,
reject hosts containing `\r\n\x00` and characters outside RFC 952 / 1123
hostname syntax.

#### L-2 — Telegram bot token logged at INFO on startup

`src/hope_hash/notifier.py:93` logs `[telegram] notifier активен (chat=%s)`
with the chat ID only — the token itself stays in `self.token` and is
only used as part of the URL passed to `urllib.request.urlopen`. urllib
does not log URLs. No leak. But the URLs include the token in
`TELEGRAM_API.format(token=...)` and `TELEGRAM_GET_UPDATES.format(...)`,
so any future debug-level log that dumps the request URL would leak it.
Worth a comment near the URL constants saying "do not log full URL".

#### L-3 — `BitcoinRPC.url` echoed into stats event payload

`src/hope_hash/cli.py:363` — `stats_provider.update_pool(f"solo:{args.rpc_url}")`.
If `--rpc-url` includes credentials in the URL (e.g.
`http://user:pass@127.0.0.1:8332/`), they end up in the SSE event stream
delivered to any `/api/events` subscriber, in `/api/stats` JSON, and in
the TUI banner. Operators normally use cookie or header auth, but the
CLI doesn't reject credentialed URLs.

Fix: strip userinfo from the URL before storing. Tiny:
`urllib.parse.urlsplit(url)._replace(netloc=parsed.hostname + ...).geturl()`,
or just `re.sub(r"://[^@/]+@", "://", url)`.

#### L-4 — `submitblock` accepts arbitrary serialised bytes from miner-built template

`src/hope_hash/solo.py:407-452` — a malicious bitcoind could return a
template that causes our serialiser to produce >32 MiB block (current
mainnet limit), and `submitblock` would then ship it. This isn't an
attack on us, and the only "victim" is the bitcoind, which already
trusted us with its cookie. Mentioned for completeness; no change needed.

### INFO

- `webui.py` HTML is **completely static** — no `format()` / `%s` /
  template substitution, no user input echoed in. The defensive
  `_escape()` helper at the bottom of the file is dead code today, kept
  for forward compatibility per its docstring. Confirmed XSS-safe.
- `/api/stats` JSON: no reflected input. All fields come from
  `StatsProvider`, which is fed only by miner-side code, not by HTTP
  request bodies. Confirmed.
- No `POST` / `PUT` / `DELETE` routes on `webui.py` or `metrics.py`.
  CSRF surface is zero.
- Telegram inbound authz: `notifier.py:235-240` checks
  `str(chat.get("id","")) != str(self.chat_id)` *before* dispatch, drops
  with a warning log. If `HOPE_HASH_TELEGRAM_CHAT_ID` is unset,
  `enabled=False` (line 64) → `start_inbound()` returns False without
  spawning the thread (line 161-163). Confirmed: there is no path that
  starts the inbound long-poll without a chat_id.
- ctypes signatures (`sha_native.py:116-138`): all `EVP_*` `argtypes`
  and `restype` set explicitly. `restype = c_void_p` for the two
  pointer-returning functions (`EVP_MD_CTX_new`, `EVP_sha256`) which is
  correct on 64-bit. `EVP_MD_CTX_new` is paired with `EVP_MD_CTX_free`
  in `try/finally` (line 149-165). No leak, no UAF.
- Solo `getblocktemplate` parsing: `int(tmpl["height"])`,
  `int(tmpl["coinbasevalue"])`, `bytes.fromhex(tmpl["previousblockhash"])`
  — each will raise `ValueError`/`TypeError` on bad input and bubble up
  to `_inbound_loop`/`reader_loop`'s exception handlers. No silent
  corruption. Witness-commitment parser (`solo.py:193-203`) checks
  prefix + length before slicing — no out-of-bounds.
- `multi-pool` rotation logic uses `RLock`, no deadlock paths visible.
- No `eval`, no `exec`, no `pickle.loads`, no `subprocess`, no
  `shell=True` anywhere under `src/hope_hash/`. Verified by grep.
- No logger format-string injection: every `logger.*` call uses an
  f-string (formatted by Python before reaching the logger) or `%s`
  with positional args; no place takes user input as the format string
  itself.
- `.dockerignore` correctly excludes `.git`, `*.db`, `.env`,
  `.env.*`, `data/`, IDE files. No secrets baked into the image layers.
- `Dockerfile` has no `ENV` lines that copy secrets; secrets come at
  runtime via `environment:` in compose.
- `deploy/grafana/datasource.yml` hardcodes nothing sensitive (only the
  in-network URL `http://prometheus:9090`). Datasource is read-only
  proxy — no scrape credentials, no API tokens.
