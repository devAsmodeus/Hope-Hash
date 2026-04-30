"""Persistent журнал принятых шаров и найденных блоков. SQLite, stdlib only."""

import logging
import sqlite3
import threading
import time
from pathlib import Path

# Единый logger пакета (см. _logging.py). Тэг [storage] добавляем в сообщения.
logger = logging.getLogger("hope_hash")

DEFAULT_DB_PATH = Path("hope_hash.db")

# Схема описывает две таблицы: shares (журнал хешей) и sessions (запуски майнера).
# `IF NOT EXISTS` делает инициализацию идемпотентной — можно открывать БД повторно.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS shares (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,           -- unix timestamp (time.time())
    job_id       TEXT    NOT NULL,
    nonce_hex    TEXT    NOT NULL,
    hash_hex     TEXT    NOT NULL,
    difficulty   REAL    NOT NULL,
    accepted     INTEGER NOT NULL,           -- 0/1
    is_block     INTEGER NOT NULL DEFAULT 0  -- 0/1, true = настоящий блок (не шар)
);
CREATE INDEX IF NOT EXISTS idx_shares_ts ON shares(ts);
CREATE INDEX IF NOT EXISTS idx_shares_accepted ON shares(accepted);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL    NOT NULL,
    ended_at    REAL,
    pool_host   TEXT    NOT NULL,
    btc_address TEXT    NOT NULL,
    worker_name TEXT    NOT NULL
);
"""


class ShareStore:
    """Потокобезопасный фасад над SQLite. Ленивая инициализация схемы."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        # Один лок на всё соединение: SQLite сам сериализует запись, но мы хотим
        # ещё и атомарность последовательностей execute+commit на уровне Python.
        self._lock = threading.Lock()
        # check_same_thread=False — соединение шарим между нитями майнера.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        # WAL включаем для параллельных читателей и более устойчивых записей.
        # Если БД на платформе/ФС, где WAL не работает — падать не должны.
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._closed = False
        logger.info("[storage] открыта БД %s", self.db_path)

    def record_share(
        self,
        job_id: str,
        nonce_hex: str,
        hash_hex: str,
        difficulty: float,
        accepted: bool = True,
        is_block: bool = False,
        ts: float | None = None,
    ) -> int:
        """Записывает шар. Возвращает id записи."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO shares (ts, job_id, nonce_hex, hash_hex, difficulty, accepted, is_block) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts if ts is not None else time.time(),
                    job_id,
                    nonce_hex,
                    hash_hex,
                    difficulty,
                    int(accepted),
                    int(is_block),
                ),
            )
            self._conn.commit()
            row_id = cur.lastrowid
        # Лог за пределами лока — sqlite-write не должен блокироваться форматом.
        if is_block:
            logger.info("[storage] BLOCK! job=%s id=%s", job_id, row_id)
        else:
            logger.info(
                "[storage] share job=%s accepted=%s id=%s", job_id, accepted, row_id
            )
        return row_id

    def start_session(self, pool_host: str, btc_address: str, worker_name: str) -> int:
        """Регистрирует начало сессии. Возвращает session_id."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions (started_at, pool_host, btc_address, worker_name) "
                "VALUES (?, ?, ?, ?)",
                (time.time(), pool_host, btc_address, worker_name),
            )
            self._conn.commit()
            session_id = cur.lastrowid
        logger.info(
            "[storage] начата сессия id=%s pool=%s worker=%s",
            session_id,
            pool_host,
            worker_name,
        )
        return session_id

    def end_session(self, session_id: int) -> None:
        """Помечает сессию как завершённую."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at=? WHERE id=?",
                (time.time(), session_id),
            )
            self._conn.commit()
        logger.info("[storage] завершена сессия id=%s", session_id)

    def total_shares(self, accepted_only: bool = True) -> int:
        """Сколько шаров записано (всего/принятых)."""
        with self._lock:
            if accepted_only:
                cur = self._conn.execute("SELECT COUNT(*) FROM shares WHERE accepted=1")
            else:
                cur = self._conn.execute("SELECT COUNT(*) FROM shares")
            (count,) = cur.fetchone()
        return int(count)

    def shares_per_hour(self, hours: int = 24) -> float:
        """Среднее число шаров в час за последние N часов."""
        if hours <= 0:
            return 0.0
        cutoff = time.time() - hours * 3600
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM shares WHERE accepted=1 AND ts >= ?",
                (cutoff,),
            )
            (count,) = cur.fetchone()
        if count == 0:
            return 0.0
        return float(count) / float(hours)

    def close(self) -> None:
        """Закрывает соединение. Идемпотентно."""
        # Проверка флага без лока: повторный close() из другой нити безопасен,
        # потому что sqlite3.Connection.close() сам потокобезопасен, а двойной
        # commit на закрытом соединении ловится try/except ниже.
        if self._closed:
            return
        try:
            with self._lock:
                if self._closed:
                    return
                try:
                    self._conn.commit()
                except sqlite3.Error:
                    pass
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._closed = True
        except Exception:
            # Закрытие должно быть максимально терпимым: не маскируем баги, но
            # не падаем в финализаторах вызывающего кода.
            self._closed = True
        logger.info("[storage] БД закрыта %s", self.db_path)
