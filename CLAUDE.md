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
- `src/hope_hash/miner.py` — `mine()`, `run_session()`, `supervisor_loop()`.
- `src/hope_hash/cli.py` — argparse-точка входа `main()`, константы пула.
- `src/hope_hash/_logging.py` — приватная настройка логгера `hope_hash`.
- `src/hope_hash/__init__.py` — публичный API + `__version__`.
- `src/hope_hash/__main__.py` — для `python -m hope_hash`.
- `tests/test_block.py` — 15 тестов на чистые функции.

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

## Self-learning loop (на будущее)

Когда накопится опыт работы агента над проектом — создать `learnings.md`
с разделами **What Has Worked / What Has Failed / Patterns and Preferences
/ Open Questions**. Формат записи:

```
**[YYYY-MM-DD] — [тип задачи]**
- Observation: что замечено
- Action: что делать / чего избегать дальше
- Confidence: high / medium / low
```

Правила: архивировать при превышении 80–100 строк, удалять устаревшее,
не добавлять записи без конкретики (vague entries едят контекст).

Сейчас файла нет — создать при первом реальном уроке.
