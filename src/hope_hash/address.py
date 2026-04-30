"""Валидация Bitcoin-адресов: bech32 (BIP-173), bech32m (BIP-350), Base58Check.

Полностью stdlib. Используется в `cli.py` как pre-flight проверка: ловим
опечатки и неправильные форматы локально, до сетевого round-trip к пулу.

Поддерживаются mainnet-адреса (bc1.../1.../3...). Testnet (tb1.../m.../n.../2...)
не валидируется намеренно — solo.ckpool.org работает только с mainnet, и
testnet-адрес почти всегда означает ошибку оператора, а не намерение.
"""

from __future__ import annotations

import hashlib

# Алфавит bech32 из BIP-173. Индекс символа = 5-битное значение.
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CHARSET_REV = {c: i for i, c in enumerate(_BECH32_CHARSET)}

# Алфавит Base58 (Bitcoin): без 0/O/I/l, чтобы исключить визуальную путаницу.
_BASE58_CHARSET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_CHARSET_REV = {c: i for i, c in enumerate(_BASE58_CHARSET)}

# Константы постфиксов checksum для bech32 и bech32m. Различие — единственный
# способ отличить v0 (BIP-173) от v1+ (BIP-350) при декодировании.
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


def _bech32_polymod(values: list[int]) -> int:
    """Полином из BIP-173. Возвращает 30-битное контрольное значение."""
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            if (b >> i) & 1:
                chk ^= gen[i]
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    """Развёртка HRP для polymod: high-bits, separator, low-bits."""
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _bech32_verify_checksum(hrp: str, data: list[int]) -> int | None:
    """Возвращает витнес-вариант (v0=bech32, v1+=bech32m) или None при несовпадении."""
    polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    if polymod == _BECH32_CONST:
        return 0  # bech32 (v0 segwit)
    if polymod == _BECH32M_CONST:
        return 1  # bech32m (v1+ segwit, taproot)
    return None


def _bech32_decode(addr: str) -> tuple[str, list[int], int]:
    """Декод bech32/bech32m. Возвращает (hrp, data_5bit, variant).

    Бросает ValueError с описанием конкретной проблемы.
    """
    # Спецификация: смешанный регистр запрещён, но строки одного регистра
    # эквивалентны. Приводим к нижнему после проверки.
    if any(ord(c) < 33 or ord(c) > 126 for c in addr):
        raise ValueError("адрес содержит недопустимые символы (вне ASCII 33-126)")
    if addr.lower() != addr and addr.upper() != addr:
        raise ValueError("адрес содержит смешанный регистр (запрещено BIP-173)")
    addr = addr.lower()

    pos = addr.rfind("1")
    if pos < 1 or pos + 7 > len(addr):
        raise ValueError("неверная позиция разделителя '1' в bech32-адресе")
    hrp = addr[:pos]
    data_str = addr[pos + 1:]

    data: list[int] = []
    for c in data_str:
        if c not in _BECH32_CHARSET_REV:
            raise ValueError(f"символ '{c}' вне алфавита bech32")
        data.append(_BECH32_CHARSET_REV[c])

    variant = _bech32_verify_checksum(hrp, data)
    if variant is None:
        raise ValueError("неверная контрольная сумма bech32")

    # Последние 6 5-битных групп — checksum, отбрасываем.
    return hrp, data[:-6], variant


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool) -> list[int]:
    """Перепаковка 5-битных групп в 8-битные байты (и наоборот)."""
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << tobits) - 1
    for value in data:
        if value < 0 or (value >> frombits) != 0:
            raise ValueError("значение вне диапазона при перепаковке битов")
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits > 0:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise ValueError("неверное выравнивание битов при перепаковке")
    return ret


def _validate_segwit(addr: str) -> None:
    """Проверка bech32 segwit-адреса (mainnet, HRP='bc')."""
    hrp, data, variant = _bech32_decode(addr)
    if hrp != "bc":
        raise ValueError(f"HRP='{hrp}', ожидается 'bc' (mainnet)")
    if not data:
        raise ValueError("пустая data-часть segwit-адреса")

    witver = data[0]
    if witver < 0 or witver > 16:
        raise ValueError(f"witness version={witver} вне диапазона [0..16]")

    # Витнес-вариант чексуммы должен соответствовать версии:
    # v0 → bech32 (BIP-173), v1+ → bech32m (BIP-350).
    if witver == 0 and variant != 0:
        raise ValueError("v0 segwit требует чексумму bech32, найдена bech32m")
    if witver != 0 and variant != 1:
        raise ValueError(f"v{witver} segwit требует чексумму bech32m, найдена bech32")

    program = _convertbits(data[1:], 5, 8, False)
    if len(program) < 2 or len(program) > 40:
        raise ValueError(f"длина witness program={len(program)} байт, ожидается 2..40")
    # v0 определяет ровно две длины: P2WPKH (20) и P2WSH (32).
    if witver == 0 and len(program) not in (20, 32):
        raise ValueError(f"v0 segwit с program={len(program)} байт (ожидается 20 или 32)")


def _base58_decode(s: str) -> bytes:
    """Декод Base58 с учётом ведущих нулей (символ '1' → 0x00 байт)."""
    if not s:
        raise ValueError("пустая Base58-строка")
    n = 0
    for c in s:
        if c not in _BASE58_CHARSET_REV:
            raise ValueError(f"символ '{c}' вне алфавита Base58")
        n = n * 58 + _BASE58_CHARSET_REV[c]
    # Преобразуем целое в байты, добавляем ведущие нули по числу '1' в начале.
    full_bytes = bytearray()
    while n > 0:
        full_bytes.insert(0, n & 0xFF)
        n >>= 8
    leading_zeros = 0
    for c in s:
        if c == "1":
            leading_zeros += 1
        else:
            break
    return b"\x00" * leading_zeros + bytes(full_bytes)


def _validate_legacy(addr: str) -> None:
    """Проверка legacy-адреса P2PKH/P2SH через Base58Check.

    Формат: [1 байт version][20 байт hash160][4 байта checksum].
    Mainnet: version=0x00 (P2PKH, '1...') или 0x05 (P2SH, '3...').
    """
    decoded = _base58_decode(addr)
    if len(decoded) != 25:
        raise ValueError(f"длина Base58-декода={len(decoded)} байт, ожидается 25")
    payload, checksum = decoded[:-4], decoded[-4:]
    digest = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if digest != checksum:
        raise ValueError("неверная Base58Check-контрольная сумма")
    version = payload[0]
    if version not in (0x00, 0x05):
        raise ValueError(
            f"version-байт=0x{version:02x}, ожидается 0x00 (P2PKH) или 0x05 (P2SH) для mainnet"
        )


def validate_btc_address(addr: str) -> None:
    """Валидирует mainnet BTC-адрес. Бросает ValueError при ошибке.

    Принимает три формата:
    - bech32 P2WPKH/P2WSH ('bc1q...'): BIP-173, witness v0, 20 или 32 байта.
    - bech32m taproot ('bc1p...'): BIP-350, witness v1+, 2..40 байт.
    - legacy P2PKH ('1...') / P2SH ('3...'): Base58Check, 25 байт.
    """
    if not isinstance(addr, str):
        raise ValueError("адрес должен быть строкой")
    if not addr:
        raise ValueError("пустой адрес")

    # Решение по префиксу. Определяем формат по первой группе символов и
    # делегируем в специализированный валидатор. Это даёт ясные сообщения
    # об ошибках для каждого формата отдельно.
    lower = addr.lower()
    if lower.startswith("bc1"):
        _validate_segwit(addr)
    elif addr[0] in ("1", "3"):
        _validate_legacy(addr)
    else:
        raise ValueError(
            f"неизвестный префикс адреса: '{addr[:4]}...' "
            "(ожидается 'bc1' для segwit/taproot, '1' для P2PKH, '3' для P2SH)"
        )
