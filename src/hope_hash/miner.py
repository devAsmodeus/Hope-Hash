"""Главный цикл хеширования mine() и сетевой супервизор переподключений."""

import socket
import threading
import time
from typing import Optional

from ._logging import logger
from .block import build_merkle_root, difficulty_to_target, double_sha256, swap_words
from .metrics import Metrics
from .notifier import TelegramNotifier
from .parallel import start_pool, stop_pool
from .storage import ShareStore
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

def _build_header_base(job: dict, extranonce1: str, extranonce2: str) -> bytes:
    """
    Собирает 76-байтовый префикс block header (всё, кроме nonce).

    Вынесено в отдельную функцию: воркеры nonce-loop теперь в parallel.py,
    а main process только формирует header_base и отдаёт его в пул.
    """
    coinbase_hex = job["coinb1"] + extranonce1 + extranonce2 + job["coinb2"]
    coinbase_hash = double_sha256(bytes.fromhex(coinbase_hex))
    merkle_root = build_merkle_root(coinbase_hash, job["merkle_branch"])
    return (
        bytes.fromhex(job["version"])[::-1] +    # 4 b version (LE)
        swap_words(job["prevhash"]) +            # 32 b prev hash (word-swap)
        merkle_root +                            # 32 b merkle (LE)
        bytes.fromhex(job["ntime"])[::-1] +      # 4 b ntime (LE)
        bytes.fromhex(job["nbits"])[::-1]        # 4 b nbits (LE)
    )


def _format_rate(rate: float) -> str:
    """Человекочитаемый хешрейт: H/s → KH/s → MH/s."""
    if rate < 1000:
        return f"{rate:.0f} H/s"
    if rate < 1_000_000:
        return f"{rate/1000:.2f} KH/s"
    return f"{rate/1_000_000:.2f} MH/s"


def mine(
    client: StratumClient,
    stop_event: threading.Event,
    n_workers: int = 1,
    store: Optional[ShareStore] = None,
    metrics: Optional[Metrics] = None,
    notifier: Optional[TelegramNotifier] = None,
):
    """
    Оркестратор пула воркеров.

    Логика:
    1. Ждём первый job, собираем header_base.
    2. Поднимаем пул (start_pool) с уникальным extranonce2.
    3. В цикле: читаем found_queue, считаем EMA-хешрейт, следим за сменой job.
    4. При смене job_id (или stop_event) — gracefully гасим пул и идём на 1.

    EMA: alpha=0.3, окно ~5с. Сэмпл = (current_counter - prev_counter) / dt.
    Счётчик не сбрасываем — храним предыдущее значение и считаем дельту,
    так точнее и не надо синхронизироваться с воркерами через Lock.

    Опциональные наблюдатели (store/metrics/notifier) подключаются хуками
    на ключевые события: найденный шар → запись в БД + counter в Prometheus +
    уведомление в Telegram; EMA-хешрейт → gauge.
    """
    extranonce2_counter = 0
    ema = 0.0
    alpha = 0.3
    report_interval = 5.0

    while not stop_event.is_set():
        with client.job_lock:
            job = client.current_job
            en1 = client.extranonce1
            en2_size = client.extranonce2_size
        if not job or not en1:
            time.sleep(0.5)
            continue

        # extranonce2 — наша часть coinbase, чтобы каждый воркер крутил уникальные хеши.
        # Поднимается на каждый job; в пределах одного job всё nonce-пространство
        # делится между процессами.
        extranonce2 = f"{extranonce2_counter:0{en2_size * 2}x}"
        extranonce2_counter += 1

        header_base = _build_header_base(job, en1, extranonce2)
        target = difficulty_to_target(client.difficulty)
        current_job_id = job["job_id"]

        processes, found_queue, hashes_counter, mp_stop = start_pool(
            n_workers, header_base, target, extranonce2,
        )

        prev_count = 0
        last_report = time.time()
        last_alive_check = time.time()

        # ─── основной цикл одного job ───
        try:
            while not stop_event.is_set():
                # 1. Не блокирующее чтение находок.
                try:
                    while True:
                        nonce_hex, hash_hex, en2 = found_queue.get_nowait()
                        logger.warning(
                            f"[mine] !!! НАЙДЕН ШАР !!! nonce={nonce_hex}  hash={hash_hex}"
                        )
                        try:
                            client.submit(current_job_id, en2, job["ntime"], nonce_hex)
                        except (OSError, AttributeError) as e:
                            # submit может прийтись на момент reconnect — не валим майнер.
                            logger.warning(f"[stratum] не удалось отправить шар: {e}")
                        # Хуки наблюдателей. Все опциональны — None означает disabled.
                        if store is not None:
                            store.record_share(
                                job_id=current_job_id, nonce_hex=nonce_hex,
                                hash_hex=hash_hex, difficulty=client.difficulty,
                                accepted=True,
                            )
                        if metrics is not None:
                            metrics.counter_inc(
                                "hopehash_shares_total", 1,
                                help="Total shares submitted (accepted by client side)",
                            )
                        if notifier is not None:
                            notifier.notify_share_accepted(
                                job_id=current_job_id, difficulty=client.difficulty,
                            )
                except Exception:
                    # Empty/queue closed — единственный нормальный путь выхода из while.
                    pass

                now = time.time()

                # 2. EMA-хешрейт.
                if now - last_report >= report_interval:
                    with hashes_counter.get_lock():
                        cur = hashes_counter.value
                    sample = (cur - prev_count) / (now - last_report)
                    ema = sample if ema == 0.0 else alpha * sample + (1 - alpha) * ema
                    logger.info(
                        f"[stats] хешрейт ≈ {_format_rate(ema)} "
                        f"(окно {_format_rate(sample)})  |  "
                        f"pool diff = {client.difficulty}  |  workers = {len(processes)}"
                    )
                    if metrics is not None:
                        metrics.gauge_set(
                            "hopehash_hashrate_hps", ema,
                            help="Current EMA hashrate in hashes per second",
                        )
                        metrics.gauge_set(
                            "hopehash_pool_difficulty", float(client.difficulty),
                            help="Current pool difficulty",
                        )
                        metrics.gauge_set(
                            "hopehash_workers", float(len(processes)),
                            help="Number of active worker processes",
                        )
                    prev_count = cur
                    last_report = now

                # 3. Смена job — выходим из цикла, чтобы пересоздать пул.
                with client.job_lock:
                    cj = client.current_job
                if not cj or cj["job_id"] != current_job_id:
                    logger.info(f"[mine] job сменился ({current_job_id} → "
                                f"{cj['job_id'] if cj else 'None'}), рестарт пула")
                    break

                # 4. Все воркеры исчерпали nonce-пространство?
                if now - last_alive_check >= 1.0:
                    if not any(p.is_alive() for p in processes):
                        logger.info("[pool] все воркеры исчерпали nonce — берём новый extranonce2")
                        break
                    last_alive_check = now

                # Дёшево спим, чтобы не жечь main CPU на busy-loop.
                time.sleep(0.05)
        finally:
            stop_pool(processes, found_queue, mp_stop)
