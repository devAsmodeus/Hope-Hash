"""ctypes-based SHA-256 backend через системный libcrypto (OpenSSL).

Зачем: pure-Python ``hashlib.sha256`` всё равно вызывает C-реализацию через
CPython, но накладные расходы на создание объекта-хешера и вызов методов
Python-уровня в hot-path майнера ощутимы. ``hashlib`` оптимизирован для
mid-state-цикла (через ``copy()``); для альтернативного бенчмарка нам
нужен прямой ctypes-доступ к OpenSSL EVP API, чтобы честно измерить
«один-проход sha256d на iteration» без mid-state-трюка.

Архитектура:

- При импорте пытаемся загрузить ``libcrypto`` под платформенными именами.
  Если не вышло — ставим ``BACKEND_NAME = "hashlib-fallback"`` и публичные
  ``sha256``/``sha256d`` молча используют ``hashlib``.
- Используем EVP API (``EVP_DigestInit_ex`` / ``Update`` / ``Final_ex``).
  Не трогаем ``EVP_MD_CTX_copy_ex`` — mid-state через ctypes не нужен,
  так нечестно мерить.
- Никаких глобальных C-объектов: контекст создаём/уничтожаем на каждый
  вызов. Это дороже, но проще и безопаснее (нет проблем с потокобезопасностью).

Публичный API:

- ``is_available() -> bool``
- ``sha256(data: bytes) -> bytes``
- ``sha256d(data: bytes) -> bytes``
- ``BACKEND_NAME: str`` — для логов и бенчмарка.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import logging
import sys
from typing import Optional

logger = logging.getLogger("hope_hash")


# ─────────────────────── загрузка libcrypto ───────────────────────

# Кандидаты по платформам. Порядок важен: сначала более новые версии.
# Ничего не падает, если файла нет — просто пробуем следующий.
_CANDIDATES_WIN = (
    "libcrypto-3.dll",
    "libcrypto-1_1.dll",
    "libeay32.dll",  # legacy OpenSSL 1.0.x (XP-эры)
)
_CANDIDATES_LINUX = (
    "libcrypto.so.3",
    "libcrypto.so.1.1",
    "libcrypto.so",
)
_CANDIDATES_MACOS = (
    "/opt/homebrew/lib/libcrypto.dylib",
    "/usr/local/opt/openssl@3/lib/libcrypto.dylib",
    "/usr/local/lib/libcrypto.dylib",
    "/usr/lib/libcrypto.dylib",
)


def _try_load_libcrypto() -> tuple[Optional[ctypes.CDLL], str]:
    """Пытается найти и загрузить libcrypto. Возвращает (CDLL|None, имя)."""
    if sys.platform.startswith("win"):
        candidates = _CANDIDATES_WIN
    elif sys.platform == "darwin":
        candidates = _CANDIDATES_MACOS
    else:
        candidates = _CANDIDATES_LINUX

    for name in candidates:
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        # Проверяем, что это реально OpenSSL: ищем EVP_sha256.
        if hasattr(lib, "EVP_sha256"):
            return lib, name

    # Последний fallback: ctypes.util.find_library('crypto') может найти
    # системный путь, который мы не угадали (например, в нестандартном prefix).
    found = ctypes.util.find_library("crypto")
    if found:
        try:
            lib = ctypes.CDLL(found)
            if hasattr(lib, "EVP_sha256"):
                return lib, found
        except OSError:
            pass

    return None, ""


_LIB, _LIB_NAME = _try_load_libcrypto()


# Имя backend'а — для бенчмарка и логов. Пользователь может прочитать его,
# чтобы понимать, что реально используется.
if _LIB is not None:
    # Обычно "libcrypto-3.dll" → "ctypes-libcrypto-3"; обрезаем расширение.
    _short = _LIB_NAME.replace(".dll", "").replace(".so", "").replace(".dylib", "")
    _short = _short.split("/")[-1].split("\\")[-1]
    BACKEND_NAME: str = f"ctypes-{_short}"
else:
    BACKEND_NAME = "hashlib-fallback"


# ─────────────────────── ctypes-обёртки EVP API ───────────────────────

if _LIB is not None:
    # Сигнатуры. На большинстве платформ ctypes по умолчанию считает
    # возвращаемое значение как ``int`` — для указателей это неправильно
    # на 64-bit (truncation). Поэтому явно ставим restype = c_void_p.

    _LIB.EVP_MD_CTX_new.restype = ctypes.c_void_p
    _LIB.EVP_MD_CTX_new.argtypes = []

    _LIB.EVP_MD_CTX_free.restype = None
    _LIB.EVP_MD_CTX_free.argtypes = [ctypes.c_void_p]

    _LIB.EVP_sha256.restype = ctypes.c_void_p
    _LIB.EVP_sha256.argtypes = []

    _LIB.EVP_DigestInit_ex.restype = ctypes.c_int
    _LIB.EVP_DigestInit_ex.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]

    _LIB.EVP_DigestUpdate.restype = ctypes.c_int
    _LIB.EVP_DigestUpdate.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ]

    _LIB.EVP_DigestFinal_ex.restype = ctypes.c_int
    _LIB.EVP_DigestFinal_ex.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint),
    ]


def is_available() -> bool:
    """True если ctypes-backend готов к использованию."""
    return _LIB is not None


def _sha256_native(data: bytes) -> bytes:
    """Один SHA-256 через EVP API. Контекст одноразовый."""
    assert _LIB is not None  # вызывается только когда is_available()
    ctx = _LIB.EVP_MD_CTX_new()
    if not ctx:
        raise RuntimeError("[sha] EVP_MD_CTX_new вернул NULL")
    try:
        md = _LIB.EVP_sha256()
        if _LIB.EVP_DigestInit_ex(ctx, md, None) != 1:
            raise RuntimeError("[sha] EVP_DigestInit_ex failed")
        if data:
            if _LIB.EVP_DigestUpdate(ctx, data, len(data)) != 1:
                raise RuntimeError("[sha] EVP_DigestUpdate failed")
        out = ctypes.create_string_buffer(32)
        out_len = ctypes.c_uint(0)
        if _LIB.EVP_DigestFinal_ex(ctx, out, ctypes.byref(out_len)) != 1:
            raise RuntimeError("[sha] EVP_DigestFinal_ex failed")
        return out.raw[: out_len.value]
    finally:
        _LIB.EVP_MD_CTX_free(ctx)


def sha256(data: bytes) -> bytes:
    """SHA-256 через ctypes-libcrypto, либо fallback на hashlib."""
    if _LIB is None:
        return hashlib.sha256(data).digest()
    return _sha256_native(data)


def sha256d(data: bytes) -> bytes:
    """Double SHA-256 (SHA-256 поверх SHA-256). Используется в Bitcoin.

    Делает ровно два вызова EVP — без mid-state. Для бенчмарка это
    «честный» baseline: сравниваем накладные расходы Python-вызова EVP
    с накладными расходами hashlib.
    """
    return sha256(sha256(data))


# Логируем, что нашли — чтобы при первом запуске пользователь видел реальность.
if _LIB is not None:
    logger.info("[sha] backend: %s (loaded %s)", BACKEND_NAME, _LIB_NAME)
else:
    logger.info("[sha] libcrypto не найден, остаёмся на hashlib")
