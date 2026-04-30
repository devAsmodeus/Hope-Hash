# Hope-Hash — solo Bitcoin miner на чистом Python

Учебный соло-майнер биткоина. Подключается к `solo.ckpool.org:3333`,
реализует Stratum V1 с нуля, перебирает SHA-256 в pure-Python, отправляет
шары. Цель — **разобраться, как работает Bitcoin mining изнутри**, а не
зарабатывать. Реальный шанс найти блок ≈ 1 к 10¹⁵ в день.

См. README.md для деталей и ROADMAP.md для плана развития.

## Tech stack

- Python ≥3.11, **только стандартная библиотека** (socket, hashlib, json,
  struct, threading, logging, argparse). Runtime-зависимостей нет.
- Layout: `src/`-style, пакет `hope_hash` под `src/hope_hash/`.
- Build backend: `hatchling`. Установка: `python -m pip install -e .`
  (на Windows: `py -3.11 -m pip install -e .`).
- Тесты: stdlib `unittest`, в `tests/`. Запуск: `python -m unittest discover -s tests`.
- Запуск: `python -m hope_hash <BTC_адрес> [имя_воркера]` или после
  install — `hope-hash <BTC_адрес> [имя_воркера]`.
- Протокол: Stratum V1 поверх TCP, JSON line-delimited.

## Структура пакета

- `src/hope_hash/block.py` — чистые функции: `double_sha256`, `swap_words`,
  `difficulty_to_target`, `build_merkle_root`. Без сайд-эффектов.
- `src/hope_hash/stratum.py` — класс `StratumClient` (TCP + JSON-RPC).
- `src/hope_hash/parallel.py` — `worker()`, `start_pool()`, `stop_pool()`.
  Mid-state SHA-256 через `hashlib.sha256().copy()`.
- `src/hope_hash/miner.py` — `mine()`, `run_session()`, `supervisor_loop()`.
- `src/hope_hash/demo.py` — `run_demo()` — offline-майнинг с синтетическим заголовком.
- `src/hope_hash/storage.py` — SQLite-журнал шаров (`ShareStore`).
- `src/hope_hash/metrics.py` — Prometheus-экспортёр (`Metrics`, `MetricsServer`).
- `src/hope_hash/notifier.py` — Telegram-уведомления через stdlib `urllib`.
- `src/hope_hash/cli.py` — argparse-точка входа `main()`, константы пула.
- `src/hope_hash/_logging.py` — приватная настройка логгера `hope_hash`.
- `src/hope_hash/__init__.py` — публичный API + `__version__`.
- `src/hope_hash/__main__.py` — для `python -m hope_hash`.
- `tests/test_block.py` — тесты на чистые функции + mid-state.

## Архитектура (не менять без обсуждения)

- Один процесс, две нити: `mine()` крутит хеши в main thread,
  `reader_loop` слушает пул в отдельной нити (НЕ daemon — для clean
  shutdown).
- `current_job` защищён `threading.Lock`. `mine()` проверяет смену
  `job_id` каждые ~16k хешей (`hashes & 0x3FFF == 0`).
- Общий `stop_event` связывает все нити: при ошибке в `reader_loop`
  он ставит флаг → `mine()` корректно выходит.
- `supervisor_loop` обеспечивает reconnect с backoff 1→2→4→…→60с.
- Endianness: version/ntime/nbits — LE через `[::-1]`, prevhash —
  word-swap (`swap_words`), merkle_root — as-is из `double_sha256`.
  Это корректно, не трогать.

## Conventions

- Комментарии и docstrings — на русском, как в существующем коде.
- Имена логов: `[net]`, `[stratum]`, `[mine]`, `[stats]`, `[main]` —
  держать единый стиль при добавлении нового.
- Объяснять «зачем», а не «что» (см. word-swap в коде как образец).
- Файлы документации: README.md (что есть), ROADMAP.md (что будет),
  CLAUDE.md (правила для агента).

## Patterns to avoid

- ❌ Не добавлять зависимости (`pip install ...`) без явной просьбы.
  Pure-Python — ключевое свойство проекта.
- ❌ Не переписывать endianness/word-swap «для красоты» — это
  работает и протестировано вручную против реальных блоков.
- ❌ Не добавлять `try/except` вокруг каждой строки. Отказы сети
  и парсинга — точечная обработка в `reader_loop`, `subscribe` и
  `supervisor_loop`.
- ❌ Не трогать структуру `header_base` в `mine()` — это hot path,
  любая «оптимизация» проверяется бенчмарком до и после.
- ❌ Не возвращать `print` вместо `logger.*` — переход уже сделан,
  держать единый канал вывода.
- ❌ Не складывать новый код в корень репо. Всё runtime — под
  `src/hope_hash/`, всё тестовое — под `tests/`.

## Workflow preferences

- Перед изменениями кода — сверяться с ROADMAP.md: если фича уже
  там описана, использовать формулировки и приоритеты оттуда, а не
  придумывать свои.
- Перед рефакторингом — спросить пользователя. Учебный код ценен
  читаемостью; «улучшение архитектуры ради архитектуры» вредно.
- Английский — для технических терминов (`nonce`, `merkle root`),
  русский — для прозы. Не смешивать в пределах одного предложения.

## Запуск (для справки)

```bash
# Установка один раз:
py -3.11 -m pip install -e .

# Запуск (любой из вариантов):
hope-hash <BTC_адрес> [имя_воркера]
python -m hope_hash <BTC_адрес> [имя_воркера]

# Тесты:
python -m unittest discover -s tests -v
```

## Self-learning loop

После каждой сессии с реальными уроками — обновляй `learnings.md`.
При обнаружении нового непреложного инварианта — добавляй в этот файл.

Формат записи в `learnings.md`:
```
**[YYYY-MM-DD] — [тип задачи]**
- Observation: конкретное наблюдение (не общая фраза)
- Action: что делать / чего избегать — применимо в будущих сессиях
- Confidence: high / medium / low
```

Правила: архивировать при превышении 80–100 строк, удалять устаревшее,
не добавлять записи без конкретики.

---

## META: Как писать правила (инструкции для агента)

Этот раздел — самый важный. Он объясняет, **как** добавлять новые правила
в CLAUDE.md и learnings.md, чтобы документ не деградировал.

### Принципы хорошего правила

1. **Причина первична.** Формула: «[Причина] — поэтому [директива]».
   > «SHA-256 endianness проверен против mainnet-блоков — NEVER переписывать.»
   Без причины правило выглядит как суеверие и будет проигнорировано.

2. **Абсолютные директивы для критического.** NEVER / ALWAYS / MUST / MUST NOT —
   только для правил, нарушение которых ломает корректность или безопасность.
   Для preference используй «предпочитать» / «по умолчанию».

3. **Конкретность > обобщение.**
   > ❌ «не злоупотреблять try/except»
   > ✅ «не ловить bare `except Exception:` на Queue.get_nowait — только `queue.Empty`»

4. **Один паттерн = одна запись.** Если новое правило похоже на существующее —
   обнови существующее, не создавай дубль.

5. **Не раздувай.** Правило, которое можно вывести из здравого смысла — не нужно.
   Пиши только то, что неочевидно или было нарушено на практике.

### Когда обновлять CLAUDE.md (этот файл)

- Новый архитектурный инвариант, нарушение которого сломает проект.
- Новое соглашение (Conventions), применимое ко всему коду.
- Антипаттерн (Patterns to avoid), реально встреченный в работе агента.

### Когда писать в learnings.md

- Конкретный урок из реального запуска, теста или ошибки агента.
- Что сработало / не сработало в конкретной задаче.
- Вопрос, требующий будущего расследования (Open Questions).

### Как обновлять при ошибке

Когда агент допустил ошибку и пользователь просит зафиксировать урок:
1. Абстрагируй: найди общий паттерн, а не конкретную деталь задачи.
2. Запиши в learnings.md под **What Has Failed**.
3. Если паттерн системный — добавь в **Patterns to avoid** в CLAUDE.md.
4. Укажи `Confidence: low` если урок из одного случая; `high` если повторялся.
