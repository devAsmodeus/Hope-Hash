"""
Юнит-тесты для криптографических функций hope_hash.block.

Покрывают «чистые» функции, которые работают без сети и состояния:
double_sha256, swap_words, difficulty_to_target, build_merkle_root.

Запуск:
    python -m unittest discover -s tests -v

Никаких сетевых вызовов и зависимостей — только стандартная библиотека.
"""

import unittest

from hope_hash.block import (
    build_merkle_root,
    difficulty_to_target,
    double_sha256,
    swap_words,
)


class TestDoubleSha256(unittest.TestCase):
    """Проверяем, что SHA256d считается корректно по известным векторам."""

    def test_empty_bytes(self):
        # Канонический вектор: SHA256d пустой строки — широко известное значение.
        expected = "5df6e0e2761359d30a8275058e299fcc0381534545f55cf43e41983f5d4c9456"
        self.assertEqual(double_sha256(b"").hex(), expected)

    def test_hello(self):
        # SHA256d(b"hello") — тоже стандартный вектор для проверки.
        expected = "9595c9df90075148eb06860365df33584b75bff782a510c6cd4883a419833d50"
        self.assertEqual(double_sha256(b"hello").hex(), expected)

    def test_returns_32_bytes(self):
        # Длина выхода SHA-256 — всегда 32 байта, независимо от входа.
        self.assertEqual(len(double_sha256(b"")), 32)
        self.assertEqual(len(double_sha256(b"x" * 1000)), 32)


class TestSwapWords(unittest.TestCase):
    """Word-swap на 4-байтных группах — главный gotcha Stratum V1."""

    def test_16_bytes(self):
        # 16 байт = 4 слова по 4 байта. Каждое слово реверсится отдельно.
        # 00112233 -> 33221100, 44556677 -> 77665544 и т.д.
        result = swap_words("00112233445566778899aabbccddeeff")
        expected = bytes.fromhex("3322110077665544bbaa9988ffeeddcc")
        self.assertEqual(result, expected)

    def test_4_bytes(self):
        # Одно слово — просто реверс байтов.
        result = swap_words("deadbeef")
        expected = bytes.fromhex("efbeadde")
        self.assertEqual(result, expected)

    def test_8_bytes(self):
        # Два слова, проверяем, что они не «перемешиваются» между собой.
        result = swap_words("0102030405060708")
        expected = bytes.fromhex("0403020108070605")
        self.assertEqual(result, expected)

    def test_returns_bytes(self):
        # На вход — hex-строка, на выход — именно bytes (а не hex).
        self.assertIsInstance(swap_words("00000000"), bytes)


class TestDifficultyToTarget(unittest.TestCase):
    """Pool difficulty -> численный target. diff=1 — это базовый Bitcoin diff-1."""

    DIFF1_TARGET = 0x00000000FFFF0000000000000000000000000000000000000000000000000000

    def test_difficulty_one(self):
        # diff=1 должен дать ровно базовый diff-1 target.
        self.assertEqual(difficulty_to_target(1.0), self.DIFF1_TARGET)

    def test_difficulty_two(self):
        # diff=2 — половина от diff-1 (целочисленно через int()).
        self.assertEqual(difficulty_to_target(2.0), int(self.DIFF1_TARGET / 2.0))

    def test_difficulty_1024(self):
        # diff=1024 — diff-1 / 1024. Типичный порядок для соло-пулов.
        self.assertEqual(difficulty_to_target(1024), int(self.DIFF1_TARGET / 1024))

    def test_higher_diff_means_smaller_target(self):
        # Чем больше сложность, тем меньше target — инварианта майнинга.
        self.assertLess(difficulty_to_target(1024), difficulty_to_target(1.0))
        self.assertLess(difficulty_to_target(2.0), difficulty_to_target(1.0))


class TestBuildMerkleRoot(unittest.TestCase):
    """Сворачивание merkle-веток от пула в финальный merkle root."""

    def test_empty_branches_returns_coinbase(self):
        # В блоке #1 одна транзакция (coinbase), поэтому merkle_branch пустой
        # и merkle_root равен самому coinbase_hash. Берём реальный merkle root
        # block #1 как coinbase_hash — функция должна вернуть его без изменений.
        coinbase_hash = bytes.fromhex(
            "0e3e2357e806b6cdb1f70b54c3a3a17b6714ee1f0e68bebb44a74b1efd512098"
        )
        self.assertEqual(build_merkle_root(coinbase_hash, []), coinbase_hash)

    def test_synthetic_one_branch(self):
        # Один branch — результат должен совпасть с double_sha256(coinbase || branch).
        # Тест согласованности самой реализации: build_merkle_root и double_sha256
        # должны давать одинаковый результат на этом простом случае.
        coinbase_hash = b"\x00" * 32
        branch_hex = "11" * 32
        result = build_merkle_root(coinbase_hash, [branch_hex])
        expected = double_sha256(coinbase_hash + bytes.fromhex(branch_hex))
        self.assertEqual(result, expected)

    def test_synthetic_two_branches(self):
        # Две ветки сворачиваются последовательно: h1 = dsha(cb||b0), root = dsha(h1||b1).
        coinbase_hash = b"\xaa" * 32
        b0 = "22" * 32
        b1 = "33" * 32
        h1 = double_sha256(coinbase_hash + bytes.fromhex(b0))
        expected = double_sha256(h1 + bytes.fromhex(b1))
        self.assertEqual(build_merkle_root(coinbase_hash, [b0, b1]), expected)

    def test_returns_32_bytes(self):
        # Merkle root — всегда 32 байта (это всё ещё SHA-256).
        result = build_merkle_root(b"\x00" * 32, ["11" * 32])
        self.assertEqual(len(result), 32)


if __name__ == "__main__":
    unittest.main()
