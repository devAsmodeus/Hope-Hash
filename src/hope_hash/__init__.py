"""Hope-Hash — учебный solo BTC miner на чистом stdlib."""

__version__ = "0.7.1"  # x-release-please-version

from . import sha_native
from .banner import print_banner, render_banner
from .bench import BenchResult, available_backends, run_benchmark, run_benchmark_all_backends
from .block import build_merkle_root, difficulty_to_target, double_sha256, swap_words
from .demo import run_demo
from .metrics import Metrics, MetricsServer, build_health_snapshot
from .miner import mine
from .notifier import TelegramNotifier
from .pools import PoolList, parse_pool_spec
from .solo import (
    BitcoinRPC,
    RPCError,
    SoloClient,
    build_coinbase,
    compute_witness_commitment,
    parse_default_witness_commitment,
    serialize_block,
)
from .storage import ShareStore
from .stratum import StratumClient
from .tui import StatsProvider, StatsSnapshot, TUIApp
from .webui import WebUIServer, render_html

__all__ = [
    "double_sha256",
    "swap_words",
    "difficulty_to_target",
    "build_merkle_root",
    "StratumClient",
    "mine",
    "run_demo",
    "run_benchmark",
    "run_benchmark_all_backends",
    "available_backends",
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
    "PoolList",
    "parse_pool_spec",
    "BitcoinRPC",
    "RPCError",
    "SoloClient",
    "build_coinbase",
    "compute_witness_commitment",
    "parse_default_witness_commitment",
    "serialize_block",
    "sha_native",
    "WebUIServer",
    "render_html",
    "__version__",
]
