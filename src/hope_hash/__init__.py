"""Hope-Hash — учебный solo BTC miner на чистом stdlib."""

from .block import build_merkle_root, difficulty_to_target, double_sha256, swap_words
from .metrics import Metrics, MetricsServer
from .miner import mine
from .notifier import TelegramNotifier
from .storage import ShareStore
from .stratum import StratumClient

__version__ = "0.2.0"
__all__ = [
    "double_sha256",
    "swap_words",
    "difficulty_to_target",
    "build_merkle_root",
    "StratumClient",
    "mine",
    "ShareStore",
    "Metrics",
    "MetricsServer",
    "TelegramNotifier",
    "__version__",
]
