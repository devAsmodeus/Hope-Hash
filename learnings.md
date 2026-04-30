# learnings.md — Hope-Hash self-learning log

Живая память агента между сессиями. Формат:

```
**[YYYY-MM-DD] — [тип задачи]**
- Observation: конкретное наблюдение
- Action: что делать / чего избегать
- Confidence: high / medium / low
```

Архивировать при превышении 80–100 строк.

---

## What Has Worked

**[2026-04-30] — Performance: mid-state SHA-256**
- Observation: `hashlib.sha256().copy()` после первых 64 байт block header
  (версия + prevhash + merkle_root[:28]) корректно воспроизводит full double_sha256.
  Тесты `TestMidstateSha256` подтвердили идентичность на 3 векторах.
- Action: для любого SHA-256 hot-path проверять, есть ли константный 64-байтный
  префикс, который можно pre-compute. Два `update()` вместо конкатенации — без разницы
  по производительности, но читабельнее.
- Confidence: high

**[2026-04-30] — Bug fix: queue exception handling**
- Observation: `except Exception: pass` на `Queue.get_nowait()` маскировал реальные
  ошибки (unpickling, закрытый queue). Правильный паттерн — `except queue.Empty:`.
- Action: ALWAYS использовать `queue.Empty` при `get_nowait()` / `get(timeout=...)`.
  NEVER bare `except Exception:` в tight loops.
- Confidence: high

**[2026-04-30] — Timing: perf_counter vs time**
- Observation: `time.time()` может «прыгать» при NTP-синхронизации. EMA-сэмплы
  при этом давали отрицательное или аномально большое delta-t.
- Action: `time.perf_counter()` для всех относительных интервалов (EMA, alive-check,
  drain deadline). `time.time()` только для абсолютных меток (SQLite timestamp,
  Telegram event time).
- Confidence: high

**[2026-04-30] — Architecture: observer callback pattern**
- Observation: `on_share_result: Optional[Callable[[int, bool], None]]` на `StratumClient`
  позволяет `mine()` подписаться на pool responses без coupling между stratum и storage.
  `_submit_req_ids: set` + отдельный `_submit_lock` изолируют submit-ответы от
  других ответов пула (suggest_difficulty, authorize).
- Action: для любой новой «ожидаемой» Stratum-операции — добавлять отдельный
  `req_id` tracking, а не ловить все `result` ответы в `_handle_message`.
- Confidence: high

---

## What Has Failed

**[2026-04-30] — Audit depth: первичный аудит пропустил семантический баг**
- Observation: `store.record_share(accepted=True)` в `miner.py` записывает шар как
  «принят», хотя это означает лишь «отправлен клиентом». Пул может отклонить.
  Обнаружено только при втором глубоком аудите.
- Action: при добавлении observer-хуков проверять семантику булевых флагов.
  `submitted` ≠ `accepted`. Подумать про отдельный флаг или callback на pool response.
- Confidence: medium

---

**[2026-04-30] — Protocol: authorize response check**
- Observation: `subscribe_and_authorize` не читала ответ на `mining.authorize`.
  Пул мог отклонить авторизацию (wrong address format, banned worker), майнер
  продолжал работу в «немом» режиме, все submits молча отклонялись.
- Action: после любого Stratum-запроса с ответом — loop `while True` до нужного
  `id`, побочные сообщения через `_handle_message`. Pattern уже был для subscribe.
- Confidence: high

## Patterns and Preferences

- IPC-объекты (Queue, Value, Event) ВСЕГДА создаются в main process, не в воркерах.
  Windows spawn-mode требует pickle-able аргументов — hashlib объект не pickle-able.
- multiprocessing.Queue ВСЕГДА нужно drain перед join() на Windows (deadlock).
- Observers (store/metrics/notifier) подключаются опциональными хуками; `None = disabled`.
  Не смешивать бизнес-логику с observer-логикой.
- hot-path в `parallel.worker`: никаких Python-level оптимизаций без бенчмарка до/после.
  Baseline замеряется через `[stats]` строки, не через синтетический timeit.

---

## Open Questions

- [x] `store.record_share(accepted=True)` — ИСПРАВЛЕНО в v0.3.0. Теперь `accepted=False`
      при записи, `on_share_result` callback обновляет через `update_share_accepted()`.
- [x] `client.difficulty` race condition — ИСПРАВЛЕНО. `job_lock` покрывает difficulty
      writer (stratum) и reader (mine()); локальная `current_diff` на весь job-цикл.
- [ ] `mining.suggest_difficulty` — CKPool реально снижает difficulty в ответ?
      Проверить в production-запуске с `--suggest-diff 0.001`.
- [ ] `hashlib.copy()` overhead: насколько дешевле на CPython 3.11 vs 3.12 vs 3.13?
      CI проходит на всех версиях, но benchmark не делали.
- [x] Submit response tracking — ИСПРАВЛЕНО. `_submit_req_ids` + `_submit_lock` в
      `StratumClient` изолируют mining.submit ответы от остальных.
