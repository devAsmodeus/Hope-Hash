"""Тесты ctypes-backend SHA-256.

Проверяем:
- ``is_available()`` не падает (на машинах без libcrypto просто False).
- При наличии backend — паритет с hashlib на эталонных векторах.
- ``BACKEND_NAME`` отражает реальность.

Если libcrypto не загрузилось — тесты на паритет skip'аются.
"""

import hashlib
import unittest

from hope_hash import sha_native


class TestIsAvailable(unittest.TestCase):
    def test_returns_bool(self):
        self.assertIsInstance(sha_native.is_available(), bool)

    def test_backend_name_is_string(self):
        self.assertIsInstance(sha_native.BACKEND_NAME, str)
        self.assertGreater(len(sha_native.BACKEND_NAME), 0)

    def test_backend_name_consistent_with_availability(self):
        if sha_native.is_available():
            self.assertTrue(sha_native.BACKEND_NAME.startswith("ctypes-"))
        else:
            self.assertEqual(sha_native.BACKEND_NAME, "hashlib-fallback")


class TestSha256Parity(unittest.TestCase):
    """Когда ctypes-backend доступен — он должен давать ровно те же байты, что hashlib."""

    def test_empty(self):
        self.assertEqual(sha_native.sha256(b""), hashlib.sha256(b"").digest())

    def test_hello(self):
        self.assertEqual(sha_native.sha256(b"hello"),
                         hashlib.sha256(b"hello").digest())

    def test_long(self):
        data = b"x" * 10000
        self.assertEqual(sha_native.sha256(data),
                         hashlib.sha256(data).digest())

    def test_binary(self):
        data = bytes(range(256))
        self.assertEqual(sha_native.sha256(data),
                         hashlib.sha256(data).digest())

    def test_single_byte(self):
        for i in (0, 1, 127, 128, 255):
            data = bytes([i])
            self.assertEqual(sha_native.sha256(data),
                             hashlib.sha256(data).digest())


class TestSha256dParity(unittest.TestCase):
    def test_empty(self):
        expected = hashlib.sha256(hashlib.sha256(b"").digest()).digest()
        self.assertEqual(sha_native.sha256d(b""), expected)

    def test_hello(self):
        expected = hashlib.sha256(hashlib.sha256(b"hello").digest()).digest()
        self.assertEqual(sha_native.sha256d(b"hello"), expected)

    def test_block_header_80_bytes(self):
        # Реалистичные данные: 80 байт block header
        version = b"\x01\x00\x00\x00"
        prevhash = bytes.fromhex(
            "0e3e2357e806b6cdb1f70b54c3a3a17b6714ee1f0e68bebb44a74b1efd512098"
        )
        merkle = b"\xaa" * 32
        ntime = b"\x29\xab\x5f\x49"
        nbits = b"\xff\xff\x00\x1d"
        nonce = b"\x01\x00\x00\x00"
        header = version + prevhash + merkle + ntime + nbits + nonce
        self.assertEqual(len(header), 80)
        expected = hashlib.sha256(hashlib.sha256(header).digest()).digest()
        self.assertEqual(sha_native.sha256d(header), expected)


class TestSha256ResultLength(unittest.TestCase):
    def test_always_32_bytes(self):
        for n in (0, 1, 63, 64, 65, 1000):
            self.assertEqual(len(sha_native.sha256(b"x" * n)), 32)
            self.assertEqual(len(sha_native.sha256d(b"x" * n)), 32)


if __name__ == "__main__":
    unittest.main()
