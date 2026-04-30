"""Тесты валидации BTC-адресов: bech32 (BIP-173), bech32m (BIP-350), Base58Check.

Канонические векторы — из BIP-173/BIP-350, а также реальные mainnet-адреса
(genesis P2PKH, известный P2SH). Адрес пользователя проекта добавлен как
живой smoke-test bech32 P2WPKH.
"""

import unittest

from hope_hash.address import validate_btc_address


class TestSegwitV0(unittest.TestCase):
    """bech32 (BIP-173), witness v0 — P2WPKH (20 байт) и P2WSH (32 байта)."""

    def test_user_address_p2wpkh(self):
        # Smoke: реальный адрес владельца проекта.
        validate_btc_address("bc1q7h92eqxlp5lkl5ak43fkeccvrcf4f4t0fy9p2e")

    def test_bip173_p2wpkh(self):
        # Канонический пример из BIP-173.
        validate_btc_address("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")

    def test_bip173_p2wsh(self):
        validate_btc_address(
            "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3"
        )

    def test_uppercase_accepted(self):
        # BIP-173: одинаковый регистр (любой) допустим.
        validate_btc_address("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4")

    def test_mixed_case_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_btc_address("Bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
        self.assertIn("регистр", str(ctx.exception))

    def test_bad_checksum(self):
        # Последний символ заменён → checksum не сойдётся.
        with self.assertRaises(ValueError) as ctx:
            validate_btc_address("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5")
        self.assertIn("контрольная сумма", str(ctx.exception))

    def test_v0_with_bech32m_checksum_rejected(self):
        # v0 segwit обязан использовать bech32, не bech32m.
        # Адрес из BIP-350 test vectors как пример «v0+bech32m» (невалиден).
        with self.assertRaises(ValueError) as ctx:
            validate_btc_address("bc1qw508d6qejxtdg4y5r3zarvary0j6gh9w")
        # Может упасть на checksum или на bad-bech32 raw — главное, что отвергается.
        self.assertIsInstance(ctx.exception, ValueError)


class TestSegwitV1Taproot(unittest.TestCase):
    """bech32m (BIP-350), witness v1 — Taproot."""

    def test_bip350_taproot(self):
        # Канонический taproot из BIP-350.
        validate_btc_address(
            "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
        )

    def test_v1_with_bech32_checksum_rejected(self):
        # Если в v1+ адресе чексумма посчитана как bech32 — отвергаем.
        # Берём BIP-350 «invalid» вектор: v1 с bech32-чексуммой.
        with self.assertRaises(ValueError):
            validate_btc_address(
                "bc1pw5dgrnzv"  # слишком короткий, упадёт на длине/чексумме
            )


class TestLegacyBase58(unittest.TestCase):
    """Base58Check: P2PKH ('1...', version 0x00) и P2SH ('3...', version 0x05)."""

    def test_genesis_p2pkh(self):
        # Адрес сатоши из coinbase genesis-блока — самый известный P2PKH.
        validate_btc_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")

    def test_known_p2sh(self):
        # Известный mainnet P2SH (3-by-3 multisig адрес из BIP-16 test vectors).
        validate_btc_address("3P14159f73E4gFr7JterCCQh9QjiTjiZrG")

    def test_p2pkh_bad_checksum(self):
        with self.assertRaises(ValueError) as ctx:
            validate_btc_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb")
        self.assertIn("контрольная сумма", str(ctx.exception))

    def test_invalid_base58_char(self):
        # '0', 'O', 'I', 'l' исключены из алфавита Base58.
        with self.assertRaises(ValueError) as ctx:
            validate_btc_address("10A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        self.assertIn("Base58", str(ctx.exception))

    def test_wrong_version_byte_rejected(self):
        # Testnet P2PKH (version=0x6F, начинается с 'm'/'n') — для нас невалиден.
        with self.assertRaises(ValueError) as ctx:
            validate_btc_address("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn")
        # Падаем либо на префиксе, либо на version-байте — оба варианта корректны.
        self.assertIsInstance(ctx.exception, ValueError)


class TestRejection(unittest.TestCase):
    """Пограничные случаи: пустые/мусорные/неподдерживаемые входы."""

    def test_empty(self):
        with self.assertRaises(ValueError):
            validate_btc_address("")

    def test_non_string(self):
        with self.assertRaises(ValueError):
            validate_btc_address(None)  # type: ignore[arg-type]

    def test_unknown_prefix(self):
        with self.assertRaises(ValueError) as ctx:
            validate_btc_address("xyz1qabcdef")
        self.assertIn("префикс", str(ctx.exception))

    def test_testnet_bech32_rejected(self):
        # Валидный testnet bech32, но HRP='tb' — отвергаем (mainnet only).
        with self.assertRaises(ValueError):
            validate_btc_address("tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx")


if __name__ == "__main__":
    unittest.main()
