# Solo BTC Miner — учебный соло-майнер на Python

> Рабочее имя проекта. Финальное название не выбрано — кандидаты см. в конце файла.

Минимальный, но настоящий соло-майнер биткоина: подключается к публичному соло-пулу, реализует протокол Stratum V1 с нуля, перебирает SHA-256 в чистом Python и отправляет шары. Без зависимостей.

Цель проекта — **разобраться, как работает Bitcoin mining изнутри**: protocol, block header, merkle tree, target, double-SHA-256. Это не способ заработать (см. раздел «Реалистичные ожидания»), а образовательный код, который можно пощупать руками и развивать дальше.

---

## Что нового в v0.6.0

Perf & resilience: **multi-pool failover** (`--pool host:port`
повторяемый, ротация после N провалов на текущем), **solo-режим
через `getblocktemplate`** (`--solo --rpc-url ... --rpc-cookie ...`,
полная сборка coinbase + witness commitment + `submitblock` через
JSON-RPC), **ctypes SHA-256 backend** (`--sha-backend
{auto,hashlib,ctypes}`, грузит libcrypto через `ctypes.CDLL`,
fallback на hashlib если не нашёлся), **`--benchmark --backends`** —
сравнительный прогон всех доступных backend'ов с финальной строкой
`[bench] result: ctypes X MH/s (Yx vs hashlib-midstate)`. Hot path
не тронут, mid-state hashlib остаётся defaultом для майнинга.

## Что нового в v0.5.0

Ops & UX полировка: `--tui` — curses-дашборд (EMA-хешрейт, шары,
job_id, аптайм; quit на `q`), ASCII-баннер при старте (`--no-banner`
для cron), `/healthz` JSON-эндпоинт на `/metrics`-сервере (200/503
для k8s liveness), Telegram inbound-команды `/stats`/`/stop`/`/restart`
(opt-in через `HOPE_HASH_TELEGRAM_INBOUND=1`, authz по chat_id),
готовый Grafana-дашборд в `deploy/grafana/hope-hash.json`. Полный
API `mine()` теперь принимает `stats_provider: StatsProvider` —
единая шина данных для TUI и web (web придёт в v0.7.0).

## Статус: что уже сделано

- [x] TCP-клиент к `solo.ckpool.org:3333` через стандартную `socket`
- [x] JSON-RPC поверх Stratum V1
- [x] `mining.subscribe` — получение `extranonce1` и `extranonce2_size`
- [x] `mining.authorize` — аутентификация под BTC-адресом
- [x] Обработка `mining.set_difficulty` (динамическая сложность пула)
- [x] Обработка `mining.notify` (получение свежей работы)
- [x] Сборка coinbase-транзакции (`coinb1 + extranonce1 + extranonce2 + coinb2`)
- [x] Вычисление merkle root через ветки от пула
- [x] Корректная сборка 80-байтного block header (с правильным word-swap для prevhash)
- [x] Цикл перебора `nonce` 0…2³² с double-SHA-256
- [x] Сравнение хеша с pool target → отправка `mining.submit` при попадании
- [x] Фоновая нить чтения сообщений от пула, защита `current_job` через `Lock`
- [x] Прерывание цикла при получении свежей работы (clean job)
- [x] Печать хешрейта раз в 5 секунд

**Уровень 0 — стабилизация: завершён.**

- [x] Reconnect с экспоненциальным backoff (1→2→4→…→60с)
- [x] Обработка `mining.set_extranonce`
- [x] Корректное завершение по Ctrl+C (общий `stop_event`, `client.close()`, `join`)
- [x] `logging` вместо `print` со стандартными уровнями
- [x] 15 юнит-тестов на криптографические функции (`unittest`)
- [x] `src/`-layout, пакет `hope_hash`, `pyproject.toml` (hatchling)
- [x] CI matrix Python 3.11/3.12/3.13 × ubuntu/windows/macos
- [ ] Конфиг через CLI/YAML — перенесено в Уровень 1

**Уровень 1 — производительность и наблюдаемость: завершён.**

- [x] Multiprocessing: N воркеров (default `cpu_count - 1`), флаг `--workers`
- [x] EMA-хешрейт (alpha=0.3, окно 5с)
- [x] SQLite-журнал шаров и сессий (`storage.py`, флаг `--db`)
- [x] Prometheus-метрики на `/metrics` (`metrics.py`, флаг `--metrics-port`)
- [x] Telegram-уведомления (через stdlib urllib, env-конфиг)
- [ ] TUI на `rich` / `curses` — отложено (зависимости либо ограниченная Win-поддержка)
- [ ] Команды Telegram-бота (`/stats`, `/restart`) — отложено

**Уровень 1.5 — глубокий аудит и UX (v0.3.0):**

- [x] Mid-state SHA-256 (`hashlib.sha256().copy()` после первых 64 байт) — ≈×1.5–2 хешрейт
- [x] `mining.suggest_difficulty` + CLI флаг `--suggest-diff` (vardiff)
- [x] Demo-режим: `--demo [--demo-diff]` — offline-майнинг без подключения к пулу
- [x] Pre-flight валидация BTC-адреса (bech32/bech32m/Base58Check, mainnet only)
- [x] Prometheus метрики `hopehash_shares_accepted_total` / `_rejected_total`
- [x] Запись шара в SQLite фиксируется только после подтверждения пула (`on_share_result` колбэк)
- [x] `mining.authorize` ответ верифицируется (раньше отказ авторизации игнорировался)
- [x] `time.perf_counter()` вместо `time.time()` для всех относительных интервалов
- [x] `except queue.Empty` вместо bare `except Exception` в горячих циклах

**Не сделано / известные ограничения:**

- Нет UI — только консоль через `logging` и `/metrics` через HTTP.
- Только Stratum V1, без Stratum V2.
- C/Rust/SIMD/GPU — Уровни 2–3, ещё впереди.

---

## Структура

```
.
├── README.md                  ← этот файл
├── ROADMAP.md                 ← план развития, расставленный по сложности
├── CHANGELOG.md               ← история версий (Keep a Changelog)
├── CLAUDE.md                  ← правила для AI-ассистента
├── LICENSE                    ← MIT
├── pyproject.toml             ← метаданные + hatchling backend
├── Makefile                   ← short-cuts: install / test / run / lint
├── .github/workflows/ci.yml   ← matrix Python 3.11–3.13 × ubuntu/windows/macos
├── src/hope_hash/
│   ├── __init__.py            ← публичный API + __version__
│   ├── __main__.py            ← `python -m hope_hash`
│   ├── cli.py                 ← argparse, точка входа, инициализация observers
│   ├── miner.py               ← mine() оркестратор + supervisor_loop
│   ├── parallel.py            ← multiprocessing воркеры nonce-loop
│   ├── stratum.py             ← StratumClient (TCP + JSON-RPC)
│   ├── block.py               ← double_sha256, swap_words, target, merkle
│   ├── address.py             ← валидация BTC-адресов (bech32/bech32m/Base58Check)
│   ├── demo.py                ← offline-майнинг (--demo)
│   ├── bench.py               ← бенчмарк хешрейта (--benchmark)
│   ├── storage.py             ← SQLite журнал шаров и сессий
│   ├── metrics.py             ← Prometheus экспортёр (http.server)
│   ├── notifier.py            ← Telegram через urllib
│   ├── _logging.py            ← настройка logger("hope_hash")
│   └── py.typed               ← PEP 561 marker
└── tests/
    ├── conftest.py            ← общие фикстуры (заготовка)
    ├── test_block.py          ← 22 теста на чистые функции + mid-state
    ├── test_storage.py        ← 11 тестов на SQLite журнал
    ├── test_metrics.py        ← 16 тестов на Prometheus экспортёр
    ├── test_notifier.py       ← 16 тестов на Telegram (через mock)
    ├── test_address.py        ← 18 тестов на валидацию BTC-адреса
    ├── test_stratum.py        ← 15 тестов на Stratum-протокол (FakeSocket)
    └── test_bench.py          ← 3 теста на бенчмарк-режим
```

---

## Установка и запуск

Никаких runtime-зависимостей, нужен только Python ≥3.11.

```bash
# Установка один раз (editable):
python -m pip install -e .

# Запуск любым из способов:
hope-hash <BTC_адрес> [имя_воркера]
python -m hope_hash <BTC_адрес> [имя_воркера]
```

**Пример:**

```bash
hope-hash bc1q5n2x4pvxhq8sxc7ck3uxq8sxc7ck3uxqzfm2py mylaptop
```

**Расширенные опции:**

```bash
hope-hash <BTC_адрес> mylaptop \
  --workers 8 \              # число процессов (default: cpu_count - 1)
  --db ./shares.db \         # путь к SQLite (default: hope_hash.db)
  --metrics-port 9090 \      # Prometheus /metrics (0 — отключить)
  --suggest-diff 0.001       # vardiff: запросить у пула низкую сложность
```

**Demo-режим (без подключения к пулу):**

```bash
hope-hash --demo                       # синтетический заголовок, низкая сложность
hope-hash --demo --demo-diff 0.0001    # ещё ниже — найдёт быстрее
hope-hash --demo --workers 4           # сколько процессов перебирают nonce
```

Demo не нуждается в BTC-адресе и не делает никаких сетевых вызовов — удобно для smoke-теста на машине, где нужно убедиться, что multiprocessing-воркеры стартуют корректно.

**Бенчмарк-режим:**

```bash
hope-hash --benchmark                          # 10 секунд на cpu_count-1 воркерах
hope-hash --benchmark --bench-duration 30      # длиннее = точнее число
hope-hash --benchmark --workers 1              # baseline для одного ядра
```

Меряет pure-Python хешрейт без сети и без шар. Полезно как точка отсчёта перед C/Rust/SIMD-оптимизациями (см. ROADMAP уровни 2–3): без числа «до» сравнивать числа «после» бессмысленно. Пример вывода на Intel i7-12700H, 4 воркера, 5 секунд:

```
[bench] platform: Windows-10-10.0.26200-SP0
[bench] python:   3.11.9 (cpython)
[bench] cpu:      16 logical cores (Intel Family 6 Model 151)
[bench] workers:  4, duration: 5.0s
[bench]   t=  1.0s  hashes=     2,654,208  rate=2.64 MH/s
[bench]   t=  2.0s  hashes=     5,914,624  rate=2.93 MH/s
[bench]   t=  3.0s  hashes=     9,093,120  rate=3.01 MH/s
[bench] === result ===
[bench]   total hashes:   11,436,032
[bench]   wall time:           5.01s
[bench]   hashrate:        2.28 MH/s
[bench]   per-worker:    570.23 KH/s (workers: 4)
```

**Валидация BTC-адреса** срабатывает локально перед подключением к пулу. Принимаются только mainnet-адреса:
- `bc1q...` (P2WPKH, P2WSH) — bech32, BIP-173
- `bc1p...` (Taproot) — bech32m, BIP-350
- `1...` (P2PKH), `3...` (P2SH) — Base58Check

Неверная контрольная сумма, смешанный регистр, testnet-префикс — отвергаются с конкретным сообщением, без сетевого round-trip.

**Telegram-уведомления (опционально):** задать env vars и просто запустить:

```bash
export HOPE_HASH_TELEGRAM_TOKEN=123456:abcdef-your-bot-token
export HOPE_HASH_TELEGRAM_CHAT_ID=123456789
hope-hash <BTC_адрес>
```

**Тесты:**

```bash
python -m unittest discover -s tests -v   # 145 тестов
```

**Prometheus-метрики, экспортируемые на `/metrics`:**

| Метрика | Тип | Описание |
|---|---|---|
| `hopehash_hashrate_hps` | gauge | EMA-хешрейт в H/s |
| `hopehash_pool_difficulty` | gauge | текущая сложность от пула |
| `hopehash_workers` | gauge | число активных воркеров |
| `hopehash_uptime_seconds` | gauge | время работы майнера |
| `hopehash_shares_total` | counter | всего отправленных шаров |
| `hopehash_shares_accepted_total` | counter | подтверждённых пулом |
| `hopehash_shares_rejected_total` | counter | отклонённых пулом |

BTC-адрес нужен валидный mainnet-адрес (`1...`, `3...`, `bc1q...`, `bc1p...`). Можно завести в любом некастодиальном кошельке — например, **Sparrow**, **Electrum**, **Wasabi**. Имя воркера — произвольная строка.

**Что увидишь:**

```
[net] подключён к solo.ckpool.org:3333
[stratum] subscribed: extranonce1=ab12cd34, en2_size=4
[stratum] authorize отправлен для воркера bc1q....mylaptop
[stratum] новая сложность: 1.0
[stratum] новая работа job_id=4f2 clean=true
[stats] хешрейт ≈ 87 KH/s  |  pool diff = 1.0
[stratum] *** ШАР ПРИНЯТ *** (id=3)
...
```

`*** ШАР ПРИНЯТ ***` означает, что ты честно работаешь и пул это видит — **это не заработок**. Реальная награда наступит только при `НАЙДЕН ШАР` с хешем ниже **сетевого** target (не пулового), что соответствует найденному блоку.

---

## Архитектура

```
┌───────────────────────────────────────────┐
│             solo.ckpool.org:3333          │
└─────────────────┬─────────────────────────┘
                  │ TCP + JSON line-delimited
                  │
┌─────────────────▼─────────────────────────┐
│          StratumClient (main thread)      │
│   • subscribe / authorize                 │
│   • держит current_job под Lock           │
└──────┬──────────────────────────┬─────────┘
       │                          │
       │ читает входящие          │ держит работу
       ▼                          ▼
┌─────────────┐            ┌─────────────────┐
│ reader_loop │            │   mine() loop   │
│  (thread)   │            │   (main thread) │
│             │            │                 │
│ обновляет   │            │  1. coinbase    │
│ current_job │            │  2. merkle root │
│ при notify  │            │  3. header base │
└─────────────┘            │  4. nonce++     │
                           │  5. SHA256d     │
                           │  6. compare     │
                           │     vs target   │
                           │  7. submit ─────┼──> через client
                           └─────────────────┘
```

Один процесс, две нити: одна крутит хеши, вторая слушает пул. Свежий `mining.notify` обновляет `current_job` под локом, цикл хеширования каждые ~16k итераций проверяет, не сменился ли `job_id` — если да, выходит и берёт свежую работу.

---

## Реалистичные ожидания

| Метрика | Значение |
|---|---|
| Хешрейт (Python, 1 поток) | 50–200 KH/s |
| Хешрейт всей сети Bitcoin | ~700 EH/s = 7×10²⁰ H/s |
| Доля от сети | ~10⁻¹⁵ |
| Блоков в день | ~144 |
| Ожидание блока соло | ~10¹³ дней |
| Награда при удаче | 3.125 BTC (~$200k) |

Это лотерея с космически низкими шансами. Соло-майнинг на CPU имеет смысл только как:
1. **Учебный проект** (понять протокол на пальцах).
2. **Лотерейный билет** (формально шанс не ноль).
3. **База для оптимизации** (можно сравнивать с C/CUDA-версиями).

Случаи, когда подобные мини-майнеры **находили** блок за всю историю — единичные, и каждый раз это был громкий новостной повод.

---

## Кандидаты на название

Финальное имя проекта не выбрано. Шорт-лист, на котором остановились:

**Серьёзные:**
- **pyrite** — пирит, «золото дураков», + отсылка к Python (py-)
- **Sisyphus** — Сизиф, вечно катит хеш в гору

**Самоироничные:**
- **CopiumMiner** — `copium` (мем-вещество, чтобы примириться с реальностью)
- **statisticallynever** — про реальный шанс
- **HopeHash** — короткое и грустное

**Технические:**
- **PicoMiner** / **NanoNonce** — про размер
- **bitfly** — битовая муха

Перед выбором проверить:
- занятость на GitHub: `https://github.com/<name>`
- занятость на PyPI: `pip show <name>`
- свободный домен: `<name>.dev` / `<name>.io`

---

## Дальше

См. **[ROADMAP.md](./ROADMAP.md)** — план развития, разбитый на лёгкое / среднее / сложное и сгруппированный по фичам.
