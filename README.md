# Solo BTC Miner — учебный соло-майнер на Python

> Рабочее имя проекта. Финальное название не выбрано — кандидаты см. в конце файла.

Минимальный, но настоящий соло-майнер биткоина: подключается к публичному соло-пулу, реализует протокол Stratum V1 с нуля, перебирает SHA-256 в чистом Python и отправляет шары. Без зависимостей.

Цель проекта — **разобраться, как работает Bitcoin mining изнутри**: protocol, block header, merkle tree, target, double-SHA-256. Это не способ заработать (см. раздел «Реалистичные ожидания»), а образовательный код, который можно пощупать руками и развивать дальше.

---

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

**Не сделано / известные ограничения:**

- Один поток → ~50–200 KH/s на ноуте. Нет multiprocessing.
- Нет персистентной статистики (логов, БД).
- Нет UI — только консоль через `logging`.
- Только Stratum V1, без `mining.suggest_difficulty` и без Stratum V2.

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
│   ├── cli.py                 ← argparse, точка входа
│   ├── miner.py               ← mine(), supervisor_loop, run_session
│   ├── stratum.py             ← StratumClient (TCP + JSON-RPC)
│   ├── block.py               ← double_sha256, swap_words, target, merkle
│   ├── _logging.py            ← настройка logger("hope_hash")
│   └── py.typed               ← PEP 561 marker
└── tests/
    ├── conftest.py            ← общие фикстуры (заготовка)
    └── test_block.py          ← 15 unittest-тестов на чистые функции
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

**Тесты:**

```bash
python -m unittest discover -s tests -v
```

BTC-адрес нужен валидный (любой формат: `1...`, `3...`, `bc1q...`, `bc1p...`). Можно завести в любом некастодиальном кошельке — например, **Sparrow**, **Electrum**, **Wasabi**. Имя воркера — произвольная строка.

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
