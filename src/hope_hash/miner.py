"""Главный цикл хеширования mine() и сетевой супервизор переподключений."""

import socket
import struct
import threading
import time

from ._logging import logger
from .block import build_merkle_root, difficulty_to_target, double_sha256, swap_words
from .stratum import StratumClient


# ─────────────────────── сетевой супервизор ───────────────────────

def run_session(client: StratumClient) -> threading.Thread:
    """
    Один цикл «жизни» соединения: connect → subscribe → запуск reader_loop.
    Возвращает уже стартованную нить-читатель. На всех ошибках бросает наверх,
    чтобы supervisor мог решить про backoff.
    """
    client.buf = b""              # буфер от прошлой сессии больше не валиден
    client.req_id = 0
    with client.job_lock:
        client.current_job = None  # extranonce1 после reconnect может смениться
    client.connect()
    client.subscribe_and_authorize()
    # Не daemon: при Ctrl+C хотим явно дождаться join, а не убить грубо.
    t = threading.Thread(target=client.reader_loop, name="stratum-reader", daemon=False)
    t.start()
    return t


def supervisor_loop(client: StratumClient):
    """
    Поднимает соединение и переподключается с экспоненциальным backoff
    (1с → 2с → 4с → ... до 60с) пока stop_event не выставлен.
    Запускается в отдельной нити, чтобы main thread мог крутить mine().
    """
    backoff = 1
    while not client.stop_event.is_set():
        reader_thread = None
        try:
            reader_thread = run_session(client)
            backoff = 1  # успешный коннект — сбрасываем задержку
            # Ждём, пока reader не выйдет (по ошибке сети или stop_event).
            while reader_thread.is_alive() and not client.stop_event.is_set():
                reader_thread.join(timeout=1.0)
        except (ConnectionError, socket.error, OSError) as e:
            logger.warning(f"[net] не удалось подключиться: {e}")
        except Exception as e:
            logger.error(f"[net] непредвиденная ошибка сессии: {e}")

        if client.stop_event.is_set():
            break

        # reader умер сам (разрыв TCP) — закрываем сокет и ждём.
        client.close()
        logger.warning(f"[net] reconnect через {backoff}с")
        # Ждём через wait(), чтобы Ctrl+C прерывал паузу мгновенно.
        if client.stop_event.wait(timeout=backoff):
            break
        backoff = min(backoff * 2, 60)


# ─────────────────────── основной майнинг-цикл ───────────────────────

def mine(client: StratumClient, stop_event: threading.Event):
    hashes = 0
    last_print = time.time()
    extranonce2_counter = 0

    while not stop_event.is_set():
        with client.job_lock:
            job = client.current_job
            en1 = client.extranonce1
            en2_size = client.extranonce2_size
        if not job or not en1:
            time.sleep(0.5)
            continue

        # extranonce2 — наша часть coinbase, чтобы каждый воркер крутил уникальные хеши.
        extranonce2 = f"{extranonce2_counter:0{en2_size * 2}x}"
        extranonce2_counter += 1

        # Собираем coinbase = coinb1 + extranonce1 + extranonce2 + coinb2
        coinbase_hex = job["coinb1"] + en1 + extranonce2 + job["coinb2"]
        coinbase_hash = double_sha256(bytes.fromhex(coinbase_hex))

        # Считаем merkle root через ветки от пула
        merkle_root = build_merkle_root(coinbase_hash, job["merkle_branch"])

        # Базовый block header без nonce (76 байт = 80 - 4)
        header_base = (
            bytes.fromhex(job["version"])[::-1] +    # 4 b version (LE)
            swap_words(job["prevhash"]) +            # 32 b prev hash (word-swap)
            merkle_root +                            # 32 b merkle (LE)
            bytes.fromhex(job["ntime"])[::-1] +      # 4 b ntime (LE)
            bytes.fromhex(job["nbits"])[::-1]        # 4 b nbits (LE)
        )

        target = difficulty_to_target(client.difficulty)
        current_job_id = job["job_id"]

        # Перебор nonce: 0 .. 2^32 - 1
        for nonce in range(0, 0xFFFFFFFF):
            if stop_event.is_set():
                return

            # Если пришла свежая работа — выходим, чтобы не тратить время на старую
            if hashes & 0x3FFF == 0:  # каждые 16k хешей дёргаем lock, чтобы не душить нить
                with client.job_lock:
                    if not client.current_job or client.current_job["job_id"] != current_job_id:
                        break

            header = header_base + struct.pack("<I", nonce)
            h = double_sha256(header)

            # хеш в Bitcoin сравнивается как big-endian число (после реверса байтов)
            h_int = int.from_bytes(h[::-1], "big")

            if h_int <= target:
                nonce_hex = struct.pack(">I", nonce).hex()
                logger.warning(
                    f"[mine] !!! НАЙДЕН ШАР !!! nonce={nonce_hex}  hash={h[::-1].hex()}"
                )
                try:
                    client.submit(current_job_id, extranonce2, job["ntime"], nonce_hex)
                except (OSError, AttributeError) as e:
                    # submit может прийтись на момент reconnect — не валим майнер.
                    logger.warning(f"[stratum] не удалось отправить шар: {e}")

            hashes += 1

            # Каждые 5 секунд — статистика
            now = time.time()
            if now - last_print >= 5.0:
                rate = hashes / (now - last_print)
                rate_str = f"{rate:.0f} H/s" if rate < 1000 else f"{rate/1000:.2f} KH/s"
                logger.info(
                    f"[stats] хешрейт ≈ {rate_str}  |  pool diff = {client.difficulty}"
                )
                hashes = 0
                last_print = now
