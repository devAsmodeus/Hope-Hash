"""Тесты solo-mode: build_coinbase, witness commitment, serialize_block, FakeRPC."""

import hashlib
import json
import struct
import threading
import unittest
from unittest.mock import patch

from hope_hash.block import double_sha256, swap_words
from hope_hash.miner import _build_header_base
from hope_hash.solo import (
    BitcoinRPC,
    RPCError,
    SoloClient,
    _push_data,
    _serialize_height,
    _varint,
    build_coinbase,
    compute_merkle_root_from_txids,
    compute_witness_commitment,
    parse_default_witness_commitment,
    serialize_block,
)


# ─────────────────────── varint ───────────────────────

class TestVarint(unittest.TestCase):
    def test_small(self):
        self.assertEqual(_varint(0), b"\x00")
        self.assertEqual(_varint(1), b"\x01")
        self.assertEqual(_varint(0xfc), b"\xfc")

    def test_uint16(self):
        self.assertEqual(_varint(0xfd), b"\xfd\xfd\x00")
        self.assertEqual(_varint(0xffff), b"\xfd\xff\xff")

    def test_uint32(self):
        self.assertEqual(_varint(0x10000), b"\xfe\x00\x00\x01\x00")

    def test_uint64(self):
        self.assertEqual(_varint(0x100000000), b"\xff\x00\x00\x00\x00\x01\x00\x00\x00")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            _varint(-1)


# ─────────────────────── push & height ───────────────────────

class TestPushAndHeight(unittest.TestCase):
    def test_push_short(self):
        # < 0x4c — просто [len][data]
        self.assertEqual(_push_data(b"abc"), b"\x03abc")

    def test_push_empty(self):
        self.assertEqual(_push_data(b""), b"\x00")

    def test_push_data1(self):
        # 0x4c..0xff → OP_PUSHDATA1
        data = b"x" * 100
        out = _push_data(data)
        self.assertEqual(out[0], 0x4c)
        self.assertEqual(out[1], 100)
        self.assertEqual(out[2:], data)

    def test_height_zero(self):
        self.assertEqual(_serialize_height(0), b"\x00")

    def test_height_small(self):
        # 1 → push of [01]
        self.assertEqual(_serialize_height(1), b"\x01\x01")
        self.assertEqual(_serialize_height(127), b"\x01\x7f")

    def test_height_extra_zero_when_high_bit_set(self):
        # 128: high bit установлен, нужен дополнительный 0x00 для positive
        self.assertEqual(_serialize_height(128), b"\x02\x80\x00")

    def test_height_typical(self):
        # 800000 → bytes(LE) = [0x00, 0x35, 0x0c]; high byte 0x0c < 0x80, без extra zero.
        self.assertEqual(_serialize_height(800000), b"\x03\x00\x35\x0c")


# ─────────────────────── coinbase ───────────────────────

class TestBuildCoinbase(unittest.TestCase):
    def test_basic_structure(self):
        cb = build_coinbase(
            height=1,
            coinbase_value=5_000_000_000,
            output_script=b"\x6a",  # OP_RETURN
        )
        # Минимум: 4 (version) + 1 (in_count) + 32+4+1+(>=2)+4 (input)
        # + 1 (out_count) + 8+1+1 (output) + 4 (locktime) ~= 60
        self.assertGreater(len(cb), 50)
        # version = 1 LE
        self.assertEqual(cb[:4], b"\x01\x00\x00\x00")
        # in_count = 1
        self.assertEqual(cb[4], 1)
        # prev_hash = 0
        self.assertEqual(cb[5:37], b"\x00" * 32)
        # prev_idx = 0xffffffff
        self.assertEqual(cb[37:41], b"\xff\xff\xff\xff")
        # locktime = 0
        self.assertEqual(cb[-4:], b"\x00\x00\x00\x00")

    def test_with_extranonce(self):
        cb_a = build_coinbase(
            height=1, coinbase_value=0, output_script=b"\x6a",
            extranonce=b"AAAA",
        )
        cb_b = build_coinbase(
            height=1, coinbase_value=0, output_script=b"\x6a",
            extranonce=b"BBBB",
        )
        # Разный extranonce → разный coinbase (это и есть mining-уникальность)
        self.assertNotEqual(cb_a, cb_b)
        self.assertEqual(len(cb_a), len(cb_b))

    def test_with_witness_commitment_adds_second_output(self):
        wc = b"\x11" * 32
        cb_no = build_coinbase(height=1, coinbase_value=0, output_script=b"\x6a")
        cb_yes = build_coinbase(
            height=1, coinbase_value=0, output_script=b"\x6a",
            witness_commitment=wc,
        )
        # Добавляется второй output: 8 (value=0) + 1 (script_len=38) + 38 (script) = 47 байт
        self.assertEqual(len(cb_yes) - len(cb_no), 47)
        # Проверим, что commitment-байты есть в выходе
        self.assertIn(wc, cb_yes)
        self.assertIn(b"\xaa\x21\xa9\xed", cb_yes)

    def test_witness_commitment_wrong_size_raises(self):
        with self.assertRaises(ValueError):
            build_coinbase(
                height=1, coinbase_value=0, output_script=b"\x6a",
                witness_commitment=b"\x11" * 16,  # не 32
            )

    def test_negative_value_raises(self):
        with self.assertRaises(ValueError):
            build_coinbase(height=1, coinbase_value=-1, output_script=b"\x6a")


# ─────────────────────── witness commitment ───────────────────────

class TestWitnessCommitment(unittest.TestCase):
    def test_compute_matches_double_sha256(self):
        root = b"\x12" * 32
        reserved = b"\x00" * 32
        expected = double_sha256(root + reserved)
        self.assertEqual(compute_witness_commitment(witness_root=root), expected)

    def test_compute_custom_reserved(self):
        root = b"\x12" * 32
        reserved = b"\xab" * 32
        expected = double_sha256(root + reserved)
        self.assertEqual(
            compute_witness_commitment(witness_root=root, witness_reserved_value=reserved),
            expected,
        )

    def test_wrong_root_size_raises(self):
        with self.assertRaises(ValueError):
            compute_witness_commitment(witness_root=b"\x12" * 16)

    def test_wrong_reserved_size_raises(self):
        with self.assertRaises(ValueError):
            compute_witness_commitment(
                witness_root=b"\x12" * 32, witness_reserved_value=b"\x00" * 16,
            )

    def test_parse_default_witness_commitment_extracts_hash(self):
        wc_hash = b"\xab" * 32
        full_script = b"\x6a\x24\xaa\x21\xa9\xed" + wc_hash
        result = parse_default_witness_commitment(full_script.hex())
        self.assertEqual(result, wc_hash)

    def test_parse_default_witness_commitment_invalid(self):
        with self.assertRaises(ValueError):
            parse_default_witness_commitment("deadbeef")  # too short
        with self.assertRaises(ValueError):
            parse_default_witness_commitment(("00" * 50))  # wrong magic


# ─────────────────────── merkle root ───────────────────────

class TestMerkleRoot(unittest.TestCase):
    def test_single_txid_returns_self(self):
        txid = b"\x11" * 32
        self.assertEqual(compute_merkle_root_from_txids([txid]), txid)

    def test_two_txids(self):
        a = b"\x11" * 32
        b = b"\x22" * 32
        expected = double_sha256(a + b)
        self.assertEqual(compute_merkle_root_from_txids([a, b]), expected)

    def test_three_txids_duplicates_last(self):
        a = b"\x11" * 32
        b = b"\x22" * 32
        c = b"\x33" * 32
        ab = double_sha256(a + b)
        cc = double_sha256(c + c)
        expected = double_sha256(ab + cc)
        self.assertEqual(compute_merkle_root_from_txids([a, b, c]), expected)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            compute_merkle_root_from_txids([])


# ─────────────────────── serialize_block ───────────────────────

class TestSerializeBlock(unittest.TestCase):
    def test_only_coinbase(self):
        header = b"\x00" * 80
        coinbase = b"\xab\xcd"
        result = serialize_block(header, coinbase, [])
        # tx_count = 1 (varint = 0x01)
        self.assertEqual(result, header + b"\x01" + coinbase)

    def test_with_other_txs(self):
        header = b"\x00" * 80
        coinbase = b"\xab"
        other = ["dead", "beef"]
        result = serialize_block(header, coinbase, other)
        expected = header + b"\x03" + coinbase + b"\xde\xad" + b"\xbe\xef"
        self.assertEqual(result, expected)

    def test_wrong_header_size_raises(self):
        with self.assertRaises(ValueError):
            serialize_block(b"\x00" * 79, b"", [])


# ─────────────────────── FakeRPC и SoloClient ───────────────────────

# Реалистичный шаблон с одной фейковой транзакцией. Поля, которые
# использует SoloClient: version, previousblockhash, height,
# coinbasevalue, bits, curtime, transactions, default_witness_commitment.
FAKE_TEMPLATE = {
    "version": 0x20000000,
    "previousblockhash": "0" * 64,
    "height": 800000,
    "coinbasevalue": 312500000,
    "bits": "1d00ffff",
    "curtime": 1700000000,
    "transactions": [
        {
            "data": "deadbeef",
            "txid": "ab" * 32,
            "hash": "ab" * 32,
        },
    ],
    "default_witness_commitment": "6a24aa21a9ed" + ("11" * 32),
}


class FakeRPC:
    """Имитация bitcoind для unit-тестов. Записывает все вызовы."""

    def __init__(self, template=None, submit_result=None):
        self.template = template or FAKE_TEMPLATE
        self.submit_result = submit_result  # None = success, str = reject reason
        self.calls: list[tuple[str, list]] = []
        self.url = "http://fake-rpc"  # SoloClient логирует self.rpc.url

    def call(self, method, params=None):
        self.calls.append((method, params or []))
        if method == "getblocktemplate":
            return self.template
        if method == "submitblock":
            return self.submit_result
        raise RPCError(-32601, f"unknown method {method}")


class TestSoloClient(unittest.TestCase):
    def _make_client(self, rpc=None, **kwargs):
        return SoloClient(
            rpc=rpc or FakeRPC(),
            btc_address="bc1qexample",
            stop_event=threading.Event(),
            **kwargs,
        )

    def test_connect_fetches_template(self):
        rpc = FakeRPC()
        client = self._make_client(rpc=rpc)
        client.connect()
        self.assertEqual(len(rpc.calls), 1)
        self.assertEqual(rpc.calls[0][0], "getblocktemplate")
        self.assertIsNotNone(client.current_job)

    def test_subscribe_authorize_is_noop(self):
        client = self._make_client()
        # Не должно бросать и не должно делать новых RPC вызовов
        client.subscribe_and_authorize()

    def test_job_has_required_stratum_fields(self):
        client = self._make_client()
        client.connect()
        job = client.current_job
        for field in ("job_id", "prevhash", "coinb1", "coinb2", "merkle_branch",
                      "version", "nbits", "ntime", "clean"):
            self.assertIn(field, job)
        # prevhash должен быть hex длиной 64
        self.assertEqual(len(job["prevhash"]), 64)
        # nbits — hex 8 символов
        self.assertEqual(len(job["nbits"]), 8)

    def test_extranonce2_size_positive(self):
        # Если en2_size=0, mine() сразу wrap'нется → должно быть >0
        client = self._make_client()
        self.assertGreater(client.extranonce2_size, 0)

    def test_submit_calls_submitblock(self):
        rpc = FakeRPC(submit_result=None)
        client = self._make_client(rpc=rpc)
        client.connect()
        # Зовём submit с фиктивными значениями.
        results: list[tuple[int, bool]] = []
        client.on_share_result = lambda req_id, ok: results.append((req_id, ok))
        client.submit(
            job_id=client.current_job["job_id"],
            extranonce2="00000000",
            ntime=client.current_job["ntime"],
            nonce_hex="00000000",
        )
        # Должен быть один вызов submitblock с hex-строкой
        submits = [c for c in rpc.calls if c[0] == "submitblock"]
        self.assertEqual(len(submits), 1)
        block_hex = submits[0][1][0]
        self.assertIsInstance(block_hex, str)
        # Должен быть валидный hex
        bytes.fromhex(block_hex)
        # Минимум: 80 байт header + 1 varint + 1 coinbase tx + 1 другая = > 100 байт hex
        self.assertGreater(len(block_hex), 200)
        # И callback должен сработать с accepted=True (submit_result=None)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0][1])

    def test_submit_reject_calls_callback_with_false(self):
        rpc = FakeRPC(submit_result="bad-prevblk")
        client = self._make_client(rpc=rpc)
        client.connect()
        results: list[tuple[int, bool]] = []
        client.on_share_result = lambda r, ok: results.append((r, ok))
        client.submit(
            job_id=client.current_job["job_id"],
            extranonce2="00000000",
            ntime=client.current_job["ntime"],
            nonce_hex="00000000",
        )
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][1])

    def test_template_to_job_prevhash_internal_le_after_swap_words(self):
        # B1 regression sentinel — final-review.md.
        # Real mainnet block #800000 prev hash (display BE):
        # bitcoind отдаёт previousblockhash в display-форме; в block header
        # должна лежать internal LE (= display BE, развёрнутый побайтно).
        # _build_header_base применяет swap_words к prevhash из job-словаря,
        # так что после swap_words мы обязаны получить ровно prev_be[::-1].
        real_prev_be_hex = (
            "00000000000000000002a7c4c1e48d76c5a37902165a270156b7a8d72728a054"
        )
        prev_be = bytes.fromhex(real_prev_be_hex)
        prev_internal_le = prev_be[::-1]

        # Symmetric fixture скрывал бы баг: убеждаемся что prevhash вообще
        # несимметричный (любой no-op-фикс на нём провалится).
        self.assertNotEqual(prev_be, prev_internal_le)

        tmpl = dict(FAKE_TEMPLATE, previousblockhash=real_prev_be_hex)
        client = self._make_client(rpc=FakeRPC(template=tmpl))
        client.connect()

        job = client.current_job
        self.assertEqual(swap_words(job["prevhash"]), prev_internal_le)

    def test_build_header_base_uses_internal_le_prevhash(self):
        # Сквозной чек: hashing-time header (mining) и submit-time header
        # (_assemble_header) должны видеть одинаковый prev_hash.
        # До B1-фикса miner получал display BE, submitter — internal LE,
        # и они расходились.
        real_prev_be_hex = (
            "00000000000000000002a7c4c1e48d76c5a37902165a270156b7a8d72728a054"
        )
        prev_internal_le = bytes.fromhex(real_prev_be_hex)[::-1]

        tmpl = dict(FAKE_TEMPLATE, previousblockhash=real_prev_be_hex)
        client = self._make_client(rpc=FakeRPC(template=tmpl))
        client.connect()

        header_base = _build_header_base(
            client.current_job, extranonce1="", extranonce2="00000000"
        )
        # Layout: 4b version | 32b prev_hash | 32b merkle | 4b ntime | 4b nbits
        prev_in_header = header_base[4:36]
        self.assertEqual(prev_in_header, prev_internal_le)


class TestBitcoinRPCAuth(unittest.TestCase):
    def test_no_auth_raises(self):
        with self.assertRaises(ValueError):
            BitcoinRPC(url="http://localhost:8332")

    def test_user_pass_only(self):
        # Не падает при создании
        rpc = BitcoinRPC(url="http://localhost:8332", username="u", password="p")
        # auth header — base64(user:pass)
        import base64
        expected = base64.b64encode(b"u:p").decode()
        self.assertEqual(rpc._auth, expected)


if __name__ == "__main__":
    unittest.main()
