"""Solo-режим через ``getblocktemplate`` (BIP-22/BIP-23).

Архитектура: ``SoloClient`` имитирует публичную поверхность
``StratumClient``, чтобы ``mine()`` работал без изменений. Вместо
TCP/JSON-line-протокола поднимаем фоновый поток, который раз в
``poll_interval_s`` секунд тянет ``getblocktemplate`` через JSON-RPC
поверх ``urllib`` и обновляет ``current_job``. На find — собираем
полный сериализованный блок и шлём ``submitblock``.

Coinbase + witness commitment (BIP-141) — самая муторная часть. Если
шаблон содержит ``coinbasetxn``, используем его как-есть. Если только
``coinbasevalue`` — собираем coinbase сами:
- input: 1 vin, prev_hash=0...0, prev_idx=0xffffffff, scriptSig =
  height-push (BIP-34) + произвольные байты (наш «extranonce» — для
  уникальности в пределах одного шаблона).
- outputs: 1 output на ``coinbasevalue`` сатоши на адрес майнера
  (тут — простой OP_RETURN, потому что P2PKH-преобразование адреса
  в скрипт — отдельная сложная задача с тестовыми векторами; для
  учебного solo-режима, где шанс найти блок ≈ 0, OP_RETURN на
  coinbasevalue допустим — заметка в README). При наличии
  ``default_witness_commitment`` добавляется второй output с
  OP_RETURN OP_PUSH36 0xaa21a9ed <commitment>.

Сериализация блока: 80-байтный header + varint(tx_count) + coinbase
+ остальные транзакции из шаблона ``transactions[*].data``.

Внимание: этот код не предназначен для зарабатывания биткоинов. Он
учебный, и шанс реально найти блок на одном CPU ≈ 1 к 10^15 в день.
Цель — научить, как `getblocktemplate` работает изнутри.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from .block import double_sha256, swap_words

logger = logging.getLogger("hope_hash")


# ─────────────────────── низкоуровневые helpers ───────────────────────

def _varint(n: int) -> bytes:
    """Bitcoin varint: 1/3/5/9 байт по диапазону.

    < 0xfd            → 1 байт
    <= 0xffff         → 0xfd + uint16 LE
    <= 0xffffffff     → 0xfe + uint32 LE
    иначе             → 0xff + uint64 LE
    """
    if n < 0:
        raise ValueError(f"varint требует n>=0, получено {n}")
    if n < 0xfd:
        return bytes([n])
    if n <= 0xffff:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xffffffff:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def _push_data(data: bytes) -> bytes:
    """Собирает push-opcode для script: либо OP_PUSHBYTES_N, либо OP_PUSHDATA1/2.

    Для простой записи в scriptPubKey/scriptSig до 75 байт это просто
    [len][data]. Дальше — отдельные опкоды; в solo-coinbase больше 75
    нам не нужно (height ≤ 4 байта + наш extranonce ≤ 8 байт).
    """
    n = len(data)
    if n < 0x4c:
        return bytes([n]) + data
    if n <= 0xff:
        return b"\x4c" + bytes([n]) + data
    if n <= 0xffff:
        return b"\x4d" + struct.pack("<H", n) + data
    return b"\x4e" + struct.pack("<I", n) + data


def _serialize_height(height: int) -> bytes:
    """BIP-34: height в coinbase scriptSig как минимально-кодированное число.

    Bitcoin использует кодирование «знаковое minimum-encoded» — для
    положительных high-byte<0x80 без расширения, для high-byte≥0x80
    добавляется нулевой байт.
    """
    if height < 0:
        raise ValueError(f"height >= 0 требуется, получено {height}")
    if height == 0:
        return _push_data(b"")
    raw = b""
    h = height
    while h:
        raw += bytes([h & 0xff])
        h >>= 8
    # Если старший бит установлен — добавим 0x00, чтобы число читалось как положительное.
    if raw[-1] & 0x80:
        raw += b"\x00"
    return _push_data(raw)


def build_coinbase(
    *,
    height: int,
    coinbase_value: int,
    output_script: bytes,
    extranonce: bytes = b"",
    witness_commitment: Optional[bytes] = None,
) -> bytes:
    """Собирает сериализованную coinbase-транзакцию.

    Структура (legacy-формат, без segwit-маркера в самой coinbase —
    witness commitment выносится в отдельный output, как требует BIP-141):

    ::
      version (4 LE) = 1
      tx_in count varint = 1
      input:
        prev_hash (32) = 0
        prev_index (4 LE) = 0xffffffff
        scriptSig: push(height) + push(extranonce)
        sequence (4 LE) = 0xffffffff
      tx_out count varint = 1 или 2
      output #1:
        value (8 LE) = coinbase_value
        scriptPubKey: output_script
      [output #2]:                              ← если witness_commitment задан
        value (8 LE) = 0
        scriptPubKey: OP_RETURN OP_PUSH36 0xaa21a9ed <commitment>
      lock_time (4 LE) = 0
    """
    if coinbase_value < 0:
        raise ValueError("coinbase_value должно быть >= 0")
    version = struct.pack("<I", 1)
    in_count = _varint(1)
    prev_hash = b"\x00" * 32
    prev_idx = struct.pack("<I", 0xffffffff)

    script_sig = _serialize_height(height)
    if extranonce:
        script_sig += _push_data(extranonce)
    script_sig_full = _varint(len(script_sig)) + script_sig
    sequence = struct.pack("<I", 0xffffffff)
    tx_in = prev_hash + prev_idx + script_sig_full + sequence

    outputs: list[bytes] = []
    out1 = struct.pack("<Q", coinbase_value) + _varint(len(output_script)) + output_script
    outputs.append(out1)

    if witness_commitment is not None:
        if len(witness_commitment) != 32:
            raise ValueError(f"witness_commitment ожидает 32 байта, получено {len(witness_commitment)}")
        # OP_RETURN(0x6a) + OP_PUSHBYTES_36(0x24) + 0xaa21a9ed + commitment
        commitment_script = b"\x6a\x24\xaa\x21\xa9\xed" + witness_commitment
        out2 = struct.pack("<Q", 0) + _varint(len(commitment_script)) + commitment_script
        outputs.append(out2)

    out_count = _varint(len(outputs))
    lock_time = struct.pack("<I", 0)

    return version + in_count + tx_in + out_count + b"".join(outputs) + lock_time


def compute_witness_commitment(
    *,
    witness_root: bytes,
    witness_reserved_value: bytes = b"\x00" * 32,
) -> bytes:
    """BIP-141: commitment = SHA256d(witness_root || witness_reserved_value).

    ``witness_root`` обычно достаётся из шаблона как ``default_witness_commitment``,
    но в шаблонах bitcoind поле ``default_witness_commitment`` уже содержит ГОТОВЫЙ
    scriptPubKey (OP_RETURN OP_PUSHBYTES_36 0xaa21a9ed <hash>) — нам нужно достать
    оттуда последние 32 байта. Эта функция нужна, когда мы строим commitment сами.
    """
    if len(witness_root) != 32:
        raise ValueError(f"witness_root ожидает 32 байта, получено {len(witness_root)}")
    if len(witness_reserved_value) != 32:
        raise ValueError("witness_reserved_value ожидает 32 байта")
    return double_sha256(witness_root + witness_reserved_value)


def parse_default_witness_commitment(commitment_hex: str) -> bytes:
    """Извлекает 32-байтный hash из готового scriptPubKey шаблона.

    bitcoind возвращает ``default_witness_commitment`` как hex-строку
    готового скрипта: ``6a24aa21a9ed<32-byte-hash>``. Префикс 6 байт
    (OP_RETURN + push + magic), последние 32 — собственно commitment.
    """
    raw = bytes.fromhex(commitment_hex)
    if len(raw) < 38 or raw[:6] != b"\x6a\x24\xaa\x21\xa9\xed":
        raise ValueError(f"неожиданный формат default_witness_commitment: {commitment_hex[:32]}...")
    return raw[6:38]


def serialize_block(
    header_80: bytes,
    coinbase_tx: bytes,
    other_txs_hex: list[str],
) -> bytes:
    """Сериализует полный блок: header + varint(tx_count) + coinbase + остальные tx.

    ``other_txs_hex`` — это поле ``transactions[*].data`` из шаблона,
    где каждый элемент — уже сериализованная транзакция в hex.
    """
    if len(header_80) != 80:
        raise ValueError(f"header должен быть 80 байт, получено {len(header_80)}")
    tx_count = 1 + len(other_txs_hex)
    body = _varint(tx_count) + coinbase_tx
    for tx_hex in other_txs_hex:
        body += bytes.fromhex(tx_hex)
    return header_80 + body


def compute_merkle_root_from_txids(txids: list[bytes]) -> bytes:
    """Считает merkle root из списка txid (32 байта каждый, internal byte order).

    Алгоритм Bitcoin: парами хешируем double_sha256, при нечётном числе
    дублируем последний. Повторяем, пока не останется один.
    """
    if not txids:
        raise ValueError("список txid не может быть пустым")
    layer = list(txids)
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        next_layer = []
        for i in range(0, len(layer), 2):
            next_layer.append(double_sha256(layer[i] + layer[i + 1]))
        layer = next_layer
    return layer[0]


# ─────────────────────── JSON-RPC клиент ───────────────────────

class RPCError(Exception):
    """Ошибка JSON-RPC от bitcoind. ``code`` — числовой код из ответа."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"RPC error {code}: {message}")
        self.code = code
        self.message = message


class BitcoinRPC:
    """Минимальный JSON-RPC клиент для bitcoind через urllib.

    Аутентификация: либо cookie-файл (``$DATADIR/.cookie`` с содержимым
    ``user:pass``), либо явные user/pass. Cookie — стандартный путь
    для локального майнинга, ``rpcauth`` — для удалённых.
    """

    def __init__(
        self,
        url: str,
        cookie_path: Optional[Path] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.timeout = float(timeout)
        # Cookie wins, чтобы локальная разработка не требовала ручной настройки.
        if cookie_path is not None:
            cred = Path(cookie_path).read_text(encoding="utf-8").strip()
            self._auth = base64.b64encode(cred.encode()).decode()
        elif username and password:
            self._auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        else:
            raise ValueError("Нужен cookie_path ИЛИ (username и password)")
        self._req_id = 0

    def call(self, method: str, params: Optional[list[Any]] = None) -> Any:
        """Отправляет один JSON-RPC вызов. Бросает RPCError на ошибку bitcoind."""
        self._req_id += 1
        body = json.dumps({
            "jsonrpc": "1.0",
            "id": self._req_id,
            "method": method,
            "params": params or [],
        }).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {self._auth}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("error"):
            err = data["error"]
            raise RPCError(int(err.get("code", -1)), str(err.get("message", "unknown")))
        return data.get("result")


# ─────────────────────── SoloClient ───────────────────────

class SoloClient:
    """Имитация ``StratumClient`` для solo-mode.

    Поверхность, которую читает ``mine()``:
      - ``current_job: dict``
      - ``extranonce1: str``, ``extranonce2_size: int``
      - ``difficulty: float``
      - ``job_lock: threading.Lock``
      - ``stop_event: threading.Event``
      - ``submit(job_id, extranonce2, ntime, nonce_hex) -> int``
      - ``on_share_result: Callable[[int, bool], None] | None``
      - ``connect()``, ``subscribe_and_authorize()``, ``reader_loop()``,
        ``close()``, ``host``, ``port``, ``sock``.

    Семантика:
      - ``connect()`` тянет первый шаблон.
      - ``subscribe_and_authorize()`` no-op.
      - ``reader_loop()`` периодически дёргает ``getblocktemplate``,
        обновляет ``current_job`` если шаблон сменился.
      - ``submit()`` синхронно вызывает ``submitblock``; ответ диспатчится
        через ``on_share_result``.
    """

    # extranonce2 формально = 0 байт (никакой Stratum-магии), но mine()
    # ждёт >0 размер, иначе extranonce2-counter wrap'нется в первой же
    # итерации. Используем 4 байта — этого с запасом для одного template.
    EXTRANONCE2_SIZE = 4

    def __init__(
        self,
        rpc: BitcoinRPC,
        btc_address: str,
        worker_name: str = "solo",
        stop_event: Optional[threading.Event] = None,
        poll_interval_s: float = 5.0,
    ) -> None:
        self.rpc = rpc
        self.username = f"{btc_address}.{worker_name}"
        self.btc_address = btc_address
        self.host = "(solo)"
        self.port = 0
        self.sock: Optional[object] = None  # для совместимости с healthz
        self.buf = b""
        self.req_id = 0
        self.extranonce1 = ""
        self.extranonce2_size = self.EXTRANONCE2_SIZE
        self.difficulty = 1.0
        self.current_job: Optional[dict[str, Any]] = None
        self.job_lock = threading.Lock()
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.suggest_diff: Optional[float] = None
        self.on_share_result: Optional[Callable[[int, bool], None]] = None
        self._submit_lock = threading.Lock()
        self._submit_req_ids: set[int] = set()
        self.poll_interval_s = float(poll_interval_s)
        # Шаблон храним целиком: при submitblock нам нужны все остальные tx.
        self._last_template: Optional[dict[str, Any]] = None
        self._template_lock = threading.Lock()

    def connect(self) -> None:
        """Тянет первый шаблон. Бросает наверх — supervisor решит про backoff."""
        self._fetch_template()
        # ``sock`` — sentinel для healthz: «коннект жив». В solo-режиме
        # коннекта нет, но если RPC отвечает — считаем за «жив».
        self.sock = object()
        logger.info("[solo] подключено к %s", self.rpc.url)

    def subscribe_and_authorize(self) -> None:
        """No-op: в solo-mode нет mining.subscribe/authorize."""
        return

    def suggest_difficulty(self, diff: float) -> None:  # noqa: D401
        """No-op: в solo difficulty диктуется шаблоном (target/bits)."""
        return

    def reader_loop(self) -> None:
        """Фоновый цикл polling ``getblocktemplate``.

        Важно: в Stratum reader_loop живёт пока стоит соединение; здесь
        мы крутим, пока не выставлен ``stop_event``. На любой ошибке RPC
        логируем и ретраим — не валим супервизор.
        """
        while not self.stop_event.is_set():
            t = time.time()
            try:
                self._fetch_template()
            except (urllib.error.URLError, RPCError, OSError, json.JSONDecodeError) as e:
                logger.warning("[solo] getblocktemplate failed: %s", e)
                # На ошибке RPC сокет считаем умершим, чтобы healthz это поймал.
                self.sock = None
                return  # выходим, supervisor переподключится
            # Спим до следующего тика, прерываемся на stop_event.
            elapsed = time.time() - t
            wait_s = max(0.0, self.poll_interval_s - elapsed)
            if self.stop_event.wait(timeout=wait_s):
                return

    def submit(self, job_id: str, extranonce2: str, ntime: str, nonce_hex: str) -> int:
        """Серилизует полный блок и шлёт ``submitblock``.

        Возвращает синтетический req_id; ответ сразу диспатчится через
        ``on_share_result`` (без задержки, как в Stratum). Это OK,
        потому что seqno нужен только чтобы матчить submit↔ack.
        """
        with self._template_lock:
            tmpl = self._last_template
        if tmpl is None:
            raise RuntimeError("solo: нет шаблона для submit")

        # Собираем header заново (не доверяем тому, что было в job: ntime
        # мог сдвинуться). job_id у нас — prevhash[:16] для совместимости.
        coinbase_tx = self._build_coinbase_for_template(tmpl, extranonce2)
        coinbase_hash = double_sha256(coinbase_tx)
        merkle_root = self._merkle_root_with_coinbase(coinbase_hash, tmpl)
        header = self._assemble_header(
            tmpl, merkle_root, ntime_le=bytes.fromhex(ntime)[::-1], nonce_hex_be=nonce_hex,
        )

        other_txs_hex = [tx["data"] for tx in tmpl.get("transactions", [])]
        block_hex = serialize_block(header, coinbase_tx, other_txs_hex).hex()

        self.req_id += 1
        my_id = self.req_id
        with self._submit_lock:
            self._submit_req_ids.add(my_id)

        try:
            result = self.rpc.call("submitblock", [block_hex])
            # bitcoind: None = success, иначе строковая причина отказа.
            accepted = result is None
            if accepted:
                logger.info("[solo] *** БЛОК ПРИНЯТ *** id=%d", my_id)
            else:
                logger.warning("[solo] submitblock отклонён id=%d: %s", my_id, result)
        except (urllib.error.URLError, RPCError, OSError) as e:
            logger.warning("[solo] submitblock сетевой провал: %s", e)
            accepted = False

        with self._submit_lock:
            self._submit_req_ids.discard(my_id)
        if self.on_share_result is not None:
            self.on_share_result(my_id, accepted)
        return my_id

    def close(self) -> None:
        """В solo-режиме ничего не закрываем — RPC stateless."""
        self.sock = None

    # ─────────────── внутренние ───────────────

    def _fetch_template(self) -> None:
        """Тянет шаблон, обновляет current_job если он сменился."""
        # rules=["segwit"] — нужен, иначе bitcoind отдаст шаблон без
        # default_witness_commitment, и блок будет невалидным после segwit.
        tmpl = self.rpc.call("getblocktemplate", [{"rules": ["segwit"]}])
        with self._template_lock:
            self._last_template = tmpl
        # job_id выбираем как prevhash[:16] — гарантированно уникален между шаблонами.
        job_id = tmpl["previousblockhash"][:16]
        with self.job_lock:
            old = self.current_job
            if old is None or old["job_id"] != job_id:
                self.current_job = self._template_to_job(tmpl, job_id)
                # Difficulty считаем из bits: target = bits → diff;
                # для учебного режима используем фиксированный 1.0,
                # mine() использует target из current_job.
                self.difficulty = 1.0
                logger.info("[solo] новый шаблон job_id=%s height=%d txs=%d",
                            job_id, tmpl.get("height", 0),
                            len(tmpl.get("transactions", [])))

    def _template_to_job(self, tmpl: dict[str, Any], job_id: str) -> dict[str, Any]:
        """Конвертирует шаблон bitcoind в job-словарь, совместимый с mine()."""
        # mine() ждёт коинб-сборку из coinb1+extranonce1+extranonce2+coinb2,
        # но в solo нам extranonce1=пустая строка, extranonce2 — наш счётчик.
        # Чтобы переиспользовать _build_header_base, кладём весь coinbase
        # «вокруг» extranonce: coinb1 = всё до extranonce, coinb2 = всё после.
        # Однако структура solo-coinbase зависит от extranonce2 → строим
        # «шаблонный» coinbase_skeleton с placeholder-extranonce и режем его.
        placeholder = b"\x00" * 4  # 4 байта — наш extranonce2 size
        coinbase_skel = self._build_coinbase_for_template(tmpl, placeholder.hex())
        # Найдём placeholder в скелете. Чтобы избежать ложных срабатываний,
        # используем уникальный паттерн вместо нулей.
        marker = bytes.fromhex("deadbeef")
        coinbase_skel_marked = self._build_coinbase_for_template(tmpl, marker.hex())
        idx = coinbase_skel_marked.find(marker)
        if idx < 0:
            raise RuntimeError("[solo] не нашёл extranonce-marker в coinbase")
        coinb1 = coinbase_skel_marked[:idx].hex()
        coinb2 = coinbase_skel_marked[idx + len(marker):].hex()

        # merkle_branch для solo: рассчитываем ветви от coinbase до root,
        # используя txid (НЕ wtxid) остальных транзакций. mine()
        # использует ту же `build_merkle_root(coinbase_hash, branches)`.
        txids = [bytes.fromhex(tx["txid"])[::-1] for tx in tmpl.get("transactions", [])]
        branch = self._merkle_branch_from_txids(txids)

        # Stratum-style формат: prevhash word-swap'ed; в solo-режиме мы
        # шаблон отдаём «как есть», а в `_build_header_base` всё равно
        # будет swap_words применён → значит здесь нужен ОБРАТНЫЙ swap,
        # чтобы compose был корректным. Делаем raw prevhash и в miner.py
        # _build_header_base вызовет swap_words → получим как надо.
        # bitcoind отдаёт previousblockhash big-endian hex (display), нам
        # нужен LE для header. swap_words конвертирует «stratum-формат»
        # обратно в LE; чтобы совпало, передадим prevhash в stratum-формате.
        prev_be = bytes.fromhex(tmpl["previousblockhash"])
        # Stratum prev = big-endian с word-swap'ом по 4 байта.
        # Inverse: байты берём как есть (display=BE), а внутри 4-байтных
        # групп переставляем little-endian → swap_words восстановит BE.
        prev_stratum_hex = b"".join(
            prev_be[i:i+4][::-1] for i in range(0, 32, 4)
        ).hex()

        # version/bits/curtime приходят как int → конвертируем в hex BE-строку,
        # такого же формата как stratum-notify (там пул отдаёт hex BE).
        version_hex = struct.pack(">I", int(tmpl["version"])).hex()
        nbits_hex = tmpl["bits"]  # уже hex BE
        ntime_hex = struct.pack(">I", int(tmpl["curtime"])).hex()

        return {
            "job_id":        job_id,
            "prevhash":      prev_stratum_hex,
            "coinb1":        coinb1,
            "coinb2":        coinb2,
            "merkle_branch": [b.hex() for b in branch],
            "version":       version_hex,
            "nbits":         nbits_hex,
            "ntime":         ntime_hex,
            "clean":         True,
        }

    def _build_coinbase_for_template(
        self,
        tmpl: dict[str, Any],
        extranonce2_hex: str,
    ) -> bytes:
        """Сборка coinbase под текущий шаблон. Принимает extranonce2 как hex."""
        height = int(tmpl.get("height", 0))
        coinbase_value = int(tmpl["coinbasevalue"])
        # OP_RETURN-output на coinbasevalue: учебный код, мы не отвлекаемся
        # на decode-bech32. Реальный соло-майнер сделал бы P2WPKH/P2PKH.
        # Но так блок останется валидным (output может быть unspendable).
        output_script = b"\x6a"  # OP_RETURN — никто не сможет потратить
        extranonce = bytes.fromhex(extranonce2_hex) if extranonce2_hex else b""

        wc_hex = tmpl.get("default_witness_commitment")
        wc: Optional[bytes] = None
        if wc_hex:
            try:
                wc = parse_default_witness_commitment(wc_hex)
            except ValueError as e:
                logger.warning("[solo] не разобрал default_witness_commitment: %s", e)
                wc = None

        return build_coinbase(
            height=height,
            coinbase_value=coinbase_value,
            output_script=output_script,
            extranonce=extranonce,
            witness_commitment=wc,
        )

    def _merkle_branch_from_txids(self, txids: list[bytes]) -> list[bytes]:
        """Considers merkle branch для coinbase (она всегда позиция 0).

        Возвращает список хешей-«соседей» по пути от coinbase к корню,
        по одному на уровень. Точно совпадает с интерфейсом, который
        отдаёт Stratum-пул в ``mining.notify[merkle_branch]``.
        """
        if not txids:
            return []
        branch: list[bytes] = []
        # Виртуальный «coinbase» сидит на позиции 0; считаем без него,
        # держим список «остальные tx», на каждом уровне берём первого.
        layer = list(txids)
        while True:
            # Соседом coinbase на этом уровне является layer[0].
            branch.append(layer[0])
            # Поднимаемся на уровень выше: считаем хеши пар, начиная со 2-го индекса.
            # Coinbase + layer[0] на этом уровне — это «новый coinbase»,
            # который нам не нужен явно (mine() сам его сложит).
            rest = layer[1:]
            if len(rest) == 0:
                break
            if len(rest) % 2 == 1:
                rest.append(rest[-1])
            next_layer = []
            for i in range(0, len(rest), 2):
                next_layer.append(double_sha256(rest[i] + rest[i + 1]))
            layer = next_layer
        return branch

    def _merkle_root_with_coinbase(
        self,
        coinbase_hash: bytes,
        tmpl: dict[str, Any],
    ) -> bytes:
        """Считает финальный merkle_root, имея coinbase_hash и шаблон."""
        txids = [bytes.fromhex(tx["txid"])[::-1] for tx in tmpl.get("transactions", [])]
        if not txids:
            return coinbase_hash
        branch = self._merkle_branch_from_txids(txids)
        h = coinbase_hash
        for b in branch:
            h = double_sha256(h + b)
        return h

    def _assemble_header(
        self,
        tmpl: dict[str, Any],
        merkle_root: bytes,
        ntime_le: bytes,
        nonce_hex_be: bytes | str,
    ) -> bytes:
        """Сборка 80-байтного header из шаблона + найденного merkle/nonce."""
        version = struct.pack("<I", int(tmpl["version"]))
        prev_be = bytes.fromhex(tmpl["previousblockhash"])
        prev_le = prev_be[::-1]  # для header BE→LE
        nbits_be = bytes.fromhex(tmpl["bits"])
        nbits_le = nbits_be[::-1]
        if isinstance(nonce_hex_be, str):
            nonce_be = bytes.fromhex(nonce_hex_be)
        else:
            nonce_be = nonce_hex_be
        # mine() даёт nonce_hex как struct.pack(">I", n).hex() → BE.
        # В header он должен быть LE.
        nonce_le = nonce_be[::-1]
        return version + prev_le + merkle_root + ntime_le + nbits_le + nonce_le
