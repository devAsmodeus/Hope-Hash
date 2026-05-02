"""Smoke-тесты для banner.py.

Проверяем что баннер не пустой, многострочный и содержит версию.
Это компромисс между «вообще не тестируем строку ASCII-графики» и
«фиксируем каждый пиксель» — последнее ломает любую корректировку.
"""

import io
import unittest

from hope_hash import __version__
from hope_hash.banner import print_banner, render_banner


class TestBanner(unittest.TestCase):
    def test_render_returns_non_empty_string(self) -> None:
        text = render_banner()
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)

    def test_render_is_multiline(self) -> None:
        # Лого + подпись = минимум 6 строк
        text = render_banner()
        self.assertGreaterEqual(text.count("\n"), 5)

    def test_render_contains_version(self) -> None:
        # В подписи должна быть текущая версия — иначе обновление забудет.
        self.assertIn(__version__, render_banner())

    def test_render_with_explicit_version(self) -> None:
        text = render_banner(version="9.9.9-test")
        self.assertIn("9.9.9-test", text)

    def test_print_banner_writes_to_stream(self) -> None:
        buf = io.StringIO()
        print_banner(stream=buf)
        out = buf.getvalue()
        self.assertGreater(len(out), 0)
        self.assertIn(__version__, out)

    def test_render_is_ascii(self) -> None:
        # Намеренно ASCII-only: баннер должен корректно показываться
        # в терминалах без UTF-8 (старые Windows консоли, цпус под docker).
        text = render_banner()
        text.encode("ascii")  # AssertionError если есть не-ASCII символы


if __name__ == "__main__":
    unittest.main()
