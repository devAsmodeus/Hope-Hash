# Hope-Hash

[English](#english) · [Русский](#russian)

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-242-brightgreen)
![Stdlib only](https://img.shields.io/badge/deps-stdlib%20only-informational)

---

## English

> A pure-stdlib solo Bitcoin miner you can read in one sitting.
> Zero runtime dependencies. Built to be understood, not profitable.

### What is this

Hope-Hash is a tiny but real solo Bitcoin miner written entirely in Python's
standard library. It speaks Stratum V1 over a raw TCP socket, builds the
80-byte block header from scratch (BIP-141 witness commitment included for
solo mode), grinds SHA-256 in pure Python with mid-state caching, and submits
shares the way a real miner does.

The point is education, not income. Your CPU at 2–5 MH/s versus the network
at ~700 EH/s means the lottery odds are roughly 1 in 10^15 per day. The
codebase is small enough that you can step through every protocol message,
every byte of the header, and every endianness flip — see
[`docs/architecture.en.md`](docs/architecture.en.md).

### Status (v0.7.0)

- Stratum V1 client with multi-pool failover (`--pool` repeatable).
- Solo mode via `getblocktemplate` (`--solo --rpc-url ... --rpc-cookie ...`).
- Multiprocessing nonce search, mid-state SHA-256, optional ctypes libcrypto.
- Curses TUI, web dashboard with SSE, Prometheus `/metrics`, `/healthz`.
- SQLite share journal, Telegram outbound + opt-in inbound commands.
- Docker compose stack (miner + Prometheus + Grafana) with provisioning.
- Bilingual docs (English / Russian) for users, deployers, and contributors.

### Install

Python ≥ 3.11. No third-party dependencies.

```bash
python -m pip install -e .
# Windows:
py -3.11 -m pip install -e .
```

### Run

A mainnet wallet address you control (P2PKH `1...`, P2SH `3...`, bech32
`bc1q...`, or Taproot `bc1p...`) is mandatory.

```bash
hope-hash bc1q5n2x4pvxhq8sxc7ck3uxq8sxc7ck3uxqzfm2py mylaptop
# equivalent:
python -m hope_hash bc1q5n2x4pvxhq8sxc7ck3uxq8sxc7ck3uxqzfm2py mylaptop
```

The address is validated locally (BIP-173 / BIP-350 / Base58Check) before
the first network round-trip — typos fail fast with a precise message.

### Advanced flags

| Flag | Purpose |
| --- | --- |
| `--workers N` | Worker processes (default `cpu_count - 1`). |
| `--db PATH` | SQLite journal file (`hope_hash.db` by default). `--no-db` disables. |
| `--metrics-port PORT` | Prometheus `/metrics` + `/healthz` (default 9090, 0 = off). |
| `--web-port PORT` | Web dashboard with SSE (default 0 = off). Loopback only by default. |
| `--web-host HOST` | Bind host for the web dashboard (default `127.0.0.1`). |
| `--tui` | Curses dashboard. Quit with `q` / `ESC`. |
| `--pool HOST:PORT` | Repeatable. Round-robin failover after `--rotate-after-failures`. |
| `--solo` | Solo mining via `getblocktemplate`. Requires `--rpc-url` plus auth. |
| `--rpc-cookie PATH` | Cookie auth for bitcoind (overrides `--rpc-user/--rpc-pass`). |
| `--sha-backend {auto,hashlib,ctypes}` | SHA-256 backend; auto picks ctypes if libcrypto loads. |
| `--suggest-diff DIFF` | Send `mining.suggest_difficulty` after authorize (vardiff). |
| `--demo` | Offline mode against a synthetic header. |
| `--benchmark` | Hashrate microbenchmark, no networking. |
| `--no-banner` | Skip the ASCII banner (cron / systemd). |

Full help: `hope-hash --help`.

### Demo

No address, no network, no shares — useful as a smoke test.

```bash
hope-hash --demo                         # synthetic header, --demo-diff 0.001
hope-hash --demo --workers 4 --demo-diff 0.0001
```

### Benchmark

```bash
hope-hash --benchmark --bench-duration 5 --workers 4
hope-hash --benchmark --backends         # hashlib mid-state vs ctypes
```

Sample output on Intel i7-12700H, 4 workers, mid-state hashlib:

```
[bench] cpu:      16 logical cores
[bench] workers:  4, duration: 5.0s
[bench] === result ===
[bench]   total hashes:   11,436,032
[bench]   wall time:           5.01s
[bench]   hashrate:        2.28 MH/s
[bench]   per-worker:    570.23 KH/s
```

### Architecture

One process, several threads: a network supervisor reconnects with
exponential backoff; a Stratum reader updates the shared `current_job`
under a `Lock`; the main thread feeds N multiprocessing workers that grind
nonces with a cached mid-state. Optional daemons publish state to TUI,
the web dashboard, Prometheus, Telegram, and `/healthz`.

See [`docs/architecture.en.md`](docs/architecture.en.md) for the full
threading model, the hot-path explanation, and the BIP references.

### Realistic expectations

| Metric | Value |
| --- | --- |
| Hashrate (Python, 1 worker) | 50–200 KH/s |
| Hashrate (4 workers, mid-state) | ~2–3 MH/s |
| Bitcoin network hashrate | ~700 EH/s = 7 × 10²⁰ H/s |
| Your share of the network | ~10⁻¹⁵ |
| Expected blocks per day | 144 |
| Solo block expectation | ~10¹³ days |

This is a lottery ticket with cosmically low odds. The interesting part is
the protocol, not the payout — for actual revenue use a pooled miner on
purpose-built hardware.

### Contributing

- [`CLAUDE.md`](CLAUDE.md) — invariants the agent and humans must respect
  (stdlib only, endianness rules, hot-path discipline).
- [`ROADMAP.md`](ROADMAP.md) — feature backlog grouped by difficulty.
- [`docs/getting-started.en.md`](docs/getting-started.en.md) — first-run
  walkthrough for users with no Bitcoin background.
- [`docs/deploy.en.md`](docs/deploy.en.md) — Docker compose, Prometheus,
  Grafana, Telegram, healthchecks.

---

## Russian

> Соло-майнер биткоина на чистом stdlib, который можно прочитать за вечер.
> Ноль runtime-зависимостей. Цель — понять, как работает майнинг, а не
> заработать.

### Что это

Hope-Hash — крошечный, но настоящий соло-майнер биткоина целиком на
стандартной библиотеке Python. Он говорит на Stratum V1 поверх голого TCP,
собирает 80-байтовый block header руками (включая witness commitment по
BIP-141 для solo-режима), крутит SHA-256 на pure-Python с mid-state
кэшированием и отправляет шары как настоящий майнер.

Цель — образовательная, не монетарная. CPU на 2–5 MH/s против сети с
~700 EH/s — это лотерея с шансом примерно 1 к 10¹⁵ в день. Кода мало
ровно настолько, чтобы можно было пройти отладчиком каждое сообщение
протокола, каждый байт заголовка и каждый endianness-перевод. Подробности
в [`docs/architecture.ru.md`](docs/architecture.ru.md).

### Статус (v0.7.0)

- Stratum V1 клиент с multi-pool failover (`--pool` повторяемый).
- Solo-режим через `getblocktemplate` (`--solo --rpc-url ... --rpc-cookie ...`).
- Multiprocessing-перебор nonce, mid-state SHA-256, опциональный ctypes-libcrypto.
- Curses TUI, web-дашборд с SSE, Prometheus `/metrics`, `/healthz`.
- SQLite-журнал шар, Telegram outbound + opt-in inbound-команды.
- Docker compose стек (miner + Prometheus + Grafana) с provisioning.
- Двуязычная документация (English / Русский) для пользователей, devops и контрибьюторов.

### Установка

Python ≥ 3.11. Сторонних зависимостей нет.

```bash
python -m pip install -e .
# Windows:
py -3.11 -m pip install -e .
```

### Запуск

Нужен реальный mainnet-адрес кошелька, который ты контролируешь (P2PKH
`1...`, P2SH `3...`, bech32 `bc1q...` или Taproot `bc1p...`).

```bash
hope-hash bc1q5n2x4pvxhq8sxc7ck3uxq8sxc7ck3uxqzfm2py mylaptop
# то же самое:
python -m hope_hash bc1q5n2x4pvxhq8sxc7ck3uxq8sxc7ck3uxqzfm2py mylaptop
```

Адрес проверяется локально (BIP-173 / BIP-350 / Base58Check) до первого
сетевого round-trip — опечатки отлавливаются с конкретным сообщением.

### Расширенные флаги

| Флаг | Назначение |
| --- | --- |
| `--workers N` | Число воркер-процессов (по умолчанию `cpu_count - 1`). |
| `--db PATH` | SQLite-журнал (`hope_hash.db` по умолчанию). `--no-db` выключает. |
| `--metrics-port PORT` | Prometheus `/metrics` + `/healthz` (default 9090, 0 — выкл). |
| `--web-port PORT` | Web-дашборд с SSE (default 0 — выкл). По умолчанию только loopback. |
| `--web-host HOST` | Bind-хост для web-дашборда (default `127.0.0.1`). |
| `--tui` | Curses-дашборд. Выход на `q` / `ESC`. |
| `--pool HOST:PORT` | Повторяемый. Round-robin failover после `--rotate-after-failures`. |
| `--solo` | Solo-майнинг через `getblocktemplate`. Требует `--rpc-url` + auth. |
| `--rpc-cookie PATH` | Cookie-auth для bitcoind (приоритет над `--rpc-user/--rpc-pass`). |
| `--sha-backend {auto,hashlib,ctypes}` | SHA-256 backend; auto = ctypes если libcrypto загружается. |
| `--suggest-diff DIFF` | Отправляет `mining.suggest_difficulty` после авторизации. |
| `--demo` | Offline-режим с синтетическим заголовком. |
| `--benchmark` | Микробенчмарк хешрейта без сети. |
| `--no-banner` | Без ASCII-баннера (cron / systemd). |

Полная справка: `hope-hash --help`.

### Demo

Без адреса, без сети, без шар — удобно для smoke-теста.

```bash
hope-hash --demo                         # синтетический заголовок, --demo-diff 0.001
hope-hash --demo --workers 4 --demo-diff 0.0001
```

### Бенчмарк

```bash
hope-hash --benchmark --bench-duration 5 --workers 4
hope-hash --benchmark --backends         # hashlib mid-state vs ctypes
```

Пример вывода на Intel i7-12700H, 4 воркера, mid-state hashlib:

```
[bench] cpu:      16 logical cores
[bench] workers:  4, duration: 5.0s
[bench] === result ===
[bench]   total hashes:   11,436,032
[bench]   wall time:           5.01s
[bench]   hashrate:        2.28 MH/s
[bench]   per-worker:    570.23 KH/s
```

### Архитектура

Один процесс, несколько нитей: сетевой supervisor переподключается с
экспоненциальным backoff; Stratum-reader обновляет общий `current_job`
под `Lock`; main thread кормит N multiprocessing-воркеров, перебирающих
nonce с кэшированным mid-state. Опциональные демоны публикуют состояние
в TUI, web-дашборд, Prometheus, Telegram и `/healthz`.

Подробная threading-модель, hot-path и BIP-ссылки — в
[`docs/architecture.ru.md`](docs/architecture.ru.md).

### Реалистичные ожидания

| Метрика | Значение |
| --- | --- |
| Хешрейт (Python, 1 воркер) | 50–200 KH/s |
| Хешрейт (4 воркера, mid-state) | ~2–3 MH/s |
| Хешрейт сети Bitcoin | ~700 EH/s = 7 × 10²⁰ H/s |
| Доля от сети | ~10⁻¹⁵ |
| Блоков в день | 144 |
| Ожидание блока соло | ~10¹³ дней |

Это лотерейный билет с космически низкими шансами. Интересна не выплата,
а протокол — для реальных доходов нужен пуловый майнер на специальном
железе.

### Контрибьюция

- [`CLAUDE.md`](CLAUDE.md) — инварианты, которые соблюдают и агент, и
  люди (только stdlib, правила endianness, дисциплина hot-path).
- [`ROADMAP.md`](ROADMAP.md) — список фич, сгруппированный по сложности.
- [`docs/getting-started.ru.md`](docs/getting-started.ru.md) — первый
  запуск для пользователей без bitcoin-опыта.
- [`docs/deploy.ru.md`](docs/deploy.ru.md) — Docker compose, Prometheus,
  Grafana, Telegram, healthchecks.
