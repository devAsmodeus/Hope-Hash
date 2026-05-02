"""Hope-Hash — учебный solo BTC miner на чистом stdlib."""

__version__ = "0.5.0"

from .banner import print_banner, render_banner
from .bench import BenchResult, run_benchmark
from .block import build_merkle_root, difficulty_to_target, double_sha256, swap_words
from .demo import run_demo
from .metrics import Metrics, MetricsServer, build_health_snapshot
from .miner import mine
from .notifier import TelegramNotifier
from .storage import ShareStore
from .stratum import StratumClient
from .tui import StatsProvider, StatsSnapshot, TUIApp

__all__ = [
    "double_sha256",
    "swap_words",
    "difficulty_to_target",
    "build_merkle_root",
    "StratumClient",
    "mine",
    "run_demo",
    "run_benchmark",
    "BenchResult",
    "ShareStore",
    "Metrics",
    "MetricsServer",
    "build_health_snapshot",
    "TelegramNotifier",
    "StatsProvider",
    "StatsSnapshot",
    "TUIApp",
    "print_banner",
    "render_banner",
    "__version__",
]
