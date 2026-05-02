# Docs & UX review

Reviewer pass over PR A/B/C documentation and CLI surface (branch
`feat/web-and-docs`, v0.7.0). Read-only, no code touched.

## TL;DR

The doc suite is genuinely good: bilingual parity is tight, the EN/RU
sections mirror each other almost line-for-line, the Russian reads as
written-by-a-human (not machine-translated), and every doc cross-link in
`docs/` resolves. CLI defaults are sensible (`--web-host 127.0.0.1`,
`--metrics-port 9090`, `--web-port 0`), and `--solo` correctly errors out
without `--rpc-url`/auth. **No blockers.** The fixes are small but
embarrassing-if-shipped: a wrong port in a `docker-compose.yml` comment,
a copy-paste-broken docker run example in both `deploy.{en,ru}.md`, a
missing `--log-file` row in the README's advanced-flags table, and a
stale `[Unreleased]` link block at the bottom of `CHANGELOG.md`.

## Blockers

None.

## Should-fix

### S1. `docker-compose.yml` header comment lies about which port is the dashboard

`docker-compose.yml` line 10 says `open http://localhost:8000  # web-дашборд`,
but the actual mapping (lines 32-33, 47-48) puts `/metrics`+`/healthz`
on `8000` and the **web dashboard on `8001`**. A first-time user who
follows the comment will get a Prometheus exposition page and conclude
the dashboard is broken. Fix: swap the comment lines so `8001` is the
dashboard and `8000` is `/metrics+/healthz`. The docs (`deploy.{en,ru}.md`
section 3) already have it right.

### S2. `docker run` example has the worker name jammed between the address and flags

Both `docs/deploy.en.md` §6 and `docs/deploy.ru.md` §6:

```
hope-hash:0.7.0 \
  bc1q...your_address... docker --workers 2 ...
```

The literal `docker` here is meant to be the positional `worker_name`
arg, but the placement reads as if it were a subcommand. A reader copy-
pasting this and editing the address may delete `docker`, then `--workers
2` becomes the worker name and argparse explodes (or worse, accepts and
the address becomes the worker name). Fix: rename to something obviously
a worker label (e.g. `mybox`) and add a `\` line-break + comment, or move
the worker-name into a comment that reads `# "mybox" — free-form worker name`.

### S3. README advanced-flags table omits `--log-file`

`README.md` advanced-flags table covers 14 flags but skips `--log-file
PATH`. The flag is real (`cli.py` line 108) and doc-mentioned in
`getting-started`'s pitfalls indirectly via `--tui`. Add a row in both
EN and RU halves: `| --log-file PATH | Duplicate logs to a file (useful
with --tui where stdout is the dashboard). |`.

### S4. `CHANGELOG.md` link footer is stale (last 5 versions missing)

Lines 257-259 still read:

```
[Unreleased]: ...compare/v0.2.0...HEAD
[0.2.0]: ...compare/v0.1.0...v0.2.0
[0.1.0]: ...releases/tag/v0.1.0
```

Versions 0.3.0 → 0.7.0 are not linked, and the `[Unreleased]` diff
points at v0.2.0 instead of v0.7.0. Add the five missing entries and
fix `[Unreleased]` to compare against `v0.7.0...HEAD`. (The section
headers `[0.7.0]`, `[0.6.0]`, etc. are also rendered as link-references
but resolve to nothing — the markdown is silently broken.)

## Nice-to-have

### N1. `--rpc-cookie` / `--rpc-user` mutual-exclusion is enforced after address validation

`cli.main()` validates the BTC address before checking solo-mode RPC
auth completeness. Practically fine, but the user has to first fix
their address before they discover their RPC config is incomplete.
Nicer order: validate `--solo` argument tuple before address validation
so a misconfigured solo invocation fails before it asks for an address
it does not need. (Note: `--solo` still requires `btc_address` because
the payout script gets baked into coinbase, even though it is currently
an OP_RETURN placeholder per `docs/architecture.{en,ru}.md`. Worth a
one-liner in the deploy docs.)

### N2. Help text for positional args is Russian-only

The argparse `description="Учебный solo BTC miner на чистом stdlib"` and
all `help=` strings are Russian. The repo is bilingual everywhere else;
`--help` is the one bilingual blind spot. Not urgent (CLI is rarely the
first surface), but if you want true parity, either pick English for
`--help` (more universal in CLI conventions) or detect locale.

### N3. `docs/architecture.{en,ru}.md` lists `telegram-out`/`telegram-in` daemons but the `cli.py` code only starts inbound conditionally

The threading-model table presents `telegram-out / telegram-in` as
optional, which is correct, but doesn't note that **inbound** requires
the explicit env-var opt-in `HOPE_HASH_TELEGRAM_INBOUND=1`. A user
reading the architecture doc may think both spawn whenever a token is
set. One-line clarification recommended.

### N4. `getting-started.{en,ru}.md` example log shows tags that may not match runtime

The fake transcript shows `[stratum] *** SHARE ACCEPTED *** (id=3)` /
`*** ШАР ПРИНЯТ ***`. Spot-check against `stratum.py` to make sure the
literal string format still matches; if you ever change it for
formatting reasons, this doc silently goes stale. Not a blocker — just
worth pinning in `learnings.md` so the next refactor remembers.

### N5. README "Status (v0.7.0)" line says "Multiprocessing nonce search"

Strictly accurate, but a new reader might confuse this with the worker
processes count. Consider "Multi-process nonce search (default
`cpu_count - 1`)" so the sentence carries the operational hint.

## Nits

- **N6.** `docs/deploy.en.md` §3 says "503 otherwise" for healthz but
  the actual semantics (`metrics.py.build_health_snapshot`) are 200 for
  `ok`/`degraded` and 503 only for `down`. The handoff doc PR-A has it
  right; deploy.en.md should mirror that nuance.
- **N7.** `docs/deploy.ru.md` §3 same nuance: «503 иначе» — should be
  «503 если down; degraded возвращает 200 с reason».
- **N8.** README RU half "Адрес проверяется локально (BIP-173 / BIP-350
  / Base58Check)" — fine, but the EN half adds the punchier "typos fail
  fast with a precise message". The RU equivalent ("опечатки
  отлавливаются с конкретным сообщением") is good — parity OK, just
  noting.
- **N9.** `README.md` benchmark sample shows `2.28 MH/s` on i7-12700H
  with 4 workers; `pr-b-summary.md` references `3.18 MH/s` with 4
  workers as v0.6.0 sanity. Both can be true (machine state varies),
  but if you want a single canonical baseline, pick one and put it in
  `bench.py` as a docstring-cited number.
- **N10.** `Dockerfile` `EXPOSE 8000 9090` is documentation-only but
  inconsistent with the actual compose mapping (8000 + 8001). Add 8001
  to `EXPOSE` so `docker inspect` is truthful.
- **N11.** `architecture.{en,ru}.md` file-map omits `_logging.py`,
  `__main__.py`, `address.py` is listed but `_logging.py`/`address.py`
  /`__main__.py` triplet should match `CLAUDE.md`'s package-structure
  list exactly. (`address.py` IS listed; `_logging.py` and `__main__.py`
  are not. Minor: list them or explain why they're omitted.)
- **N12.** README cross-links to `docs/getting-started.en.md` and
  `docs/deploy.en.md` only from the EN half; the RU half links to the
  RU versions. Correct, but a "switch to other language" link at the
  top of each `docs/*` file would be friendlier. The bottom "See also"
  already includes the cross-language link — it just isn't above the
  fold.
- **N13.** PR-A handoff §"Open questions" mentions five open items
  inherited by PR-B/C; those are now resolved (multi-pool stats_provider
  hookup, ctypes backend in `/api/stats` via `set_sha_backend`, etc.).
  Nice to leave a one-liner pointer at the top of `pr-a-summary.md`
  saying "→ resolved in PR-B/C, see `pr-c-summary.md`" so future
  readers don't chase a closed list.

## Praise

- **Bilingual parity is excellent.** Every `docs/*.en.md` has a
  matching `docs/*.ru.md` with the same headings, same numbered
  sections, same code blocks. The Russian is colloquial and natural,
  not machine-translated — phrases like "Это про долгий запуск" and
  "Когда коннект поднимется, увидишь что-то такое" read as written by
  a Russian speaker who knows what they're doing.
- **Technical-term discipline is on point.** `nonce`, `merkle root`,
  `Stratum`, `getblocktemplate`, `mid-state`, `extranonce`, `coinbase`
  are all left in English in the RU prose, which is correct and matches
  Russian Bitcoin-dev convention.
- **Defaults are conservative and safe.** `--web-host 127.0.0.1`,
  `--web-port 0` (off by default), `HOPE_HASH_TELEGRAM_INBOUND=0` (off
  unless explicit opt-in), no auth on web endpoints + explicit
  reverse-proxy guidance in `deploy.{en,ru}.md` §7 — this is exactly
  the right posture for a stdlib-only project that someone might
  expose unintentionally.
- **`cli.py` mutual-exclusion errors are precise.** `--benchmark` +
  `--demo` errors with `"--benchmark и --demo взаимоисключающи"`,
  `--solo` without `--rpc-url` errors with the exact missing flag,
  `--solo` without auth errors with the explicit auth alternatives.
  All three exit with code 2 (argparse convention), all three print to
  stderr.
- **Handoff docs are model citizens.** `docs/handoff/pr-{a,b,c}.md`
  each include file-maps, new-flag tables, gotchas-for-next-PR, and
  open-questions sections. The "Verification" block at the bottom
  documents the exact commands that were run; PR-C even has a
  reviewer checklist. This is the kind of paper trail that makes
  audits actually possible.
- **`242` test count is consistent everywhere.** README badge,
  CHANGELOG v0.7.0 line, PR-C handoff — all agree, and the local
  `unittest discover` confirms `Ran 242 tests in 19.154s — OK`.
