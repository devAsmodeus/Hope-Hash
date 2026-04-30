"""Hope-Hash — учебный solo BTC miner на чистом stdlib."""

from .block import build_merkle_root, difficulty_to_target, double_sha256, swap_words
from .miner import mine
from .stratum import StratumClient

__version__ = "0.1.0"
__all__ = [
    "double_sha256",
    "swap_words",
    "difficulty_to_target",
    "build_merkle_root",
    "StratumClient",
    "mine",
    "__version__",
]
