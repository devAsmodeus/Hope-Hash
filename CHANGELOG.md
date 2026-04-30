# Changelog

Все значимые изменения проекта отражены здесь.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект придерживается [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

## [0.1.0] — 2026-04-30

### Добавлено
- Stratum V1 клиент к `solo.ckpool.org:3333` (TCP + JSON-RPC).
- Цикл хеширования SHA-256 на чистом stdlib.
- Обработка `mining.set_difficulty`, `mining.notify`, `mining.set_extranonce`.
- Reconnect с экспоненциальным backoff (1→60с).
- Общий `stop_event` для чистого shutdown по Ctrl+C.
- Логирование через `logging` со стандартными уровнями.
- 15 юнит-тестов на криптографические функции (`unittest`).
- Реструктуризация в `src/`-layout с пакетом `hope_hash`.

[Unreleased]: https://github.com/KruglikovskiiPA/Hope-Hash/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/KruglikovskiiPA/Hope-Hash/releases/tag/v0.1.0
