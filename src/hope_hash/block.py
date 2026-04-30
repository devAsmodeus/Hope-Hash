"""Чистые криптографические утилиты Bitcoin: SHA256d, word-swap, target, merkle."""

import hashlib


# Базовый target для difficulty=1 (Bitcoin diff-1). Вынесен на уровень модуля,
# чтобы быть единым источником правды и для difficulty_to_target, и для тестов.
DIFF1_TARGET = 0x00000000FFFF0000000000000000000000000000000000000000000000000000


def double_sha256(data: bytes) -> bytes:
    """SHA-256(SHA-256(x)) — основная хеш-функция Bitcoin."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def swap_words(hex_str: str) -> bytes:
    """
    Stratum-овский «word swap»: переворачивает каждые 4 байта внутри строки.
    prev_block_hash приходит от пула в этом «свопнутом» формате —
    в заголовок блока его надо положить именно так. Главный gotcha Stratum V1.
    """
    raw = bytes.fromhex(hex_str)
    return b"".join(raw[i:i+4][::-1] for i in range(0, len(raw), 4))


def difficulty_to_target(diff: float) -> int:
    """
    Pool-сложность → численный target. Шар принимается, если SHA256d(header) <= target.
    diff=1 соответствует базовому target Bitcoin diff-1.
    """
    return int(DIFF1_TARGET / diff)


def build_merkle_root(coinbase_hash: bytes, branches: list) -> bytes:
    """Сворачивает merkle-дерево по веткам, полученным от пула."""
    h = coinbase_hash
    for b in branches:
        h = double_sha256(h + bytes.fromhex(b))
    return h
