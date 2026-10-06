"""bge-reranker-v2-m3: reads (query, product text) TOGETHER and scores the match.

Embedding search compares query and product separately (fast, rough).
The reranker looks at both at once (slower, much sharper), so we only run it on
the top ~30 candidates per slot. It's what pushes a costume hat out of
"beach clothes" results. Also multilingual, like BGE-M3.
"""
from __future__ import annotations

import os
import threading
from functools import lru_cache

from common.config import settings
from common.device import get_device, use_fp16

MAX_LENGTH = 512


class Reranker:
    def __init__(self):
        hf_home = getattr(settings, "hf_home", None)
        if hf_home:
            os.environ.setdefault("HF_HOME", str(hf_home))
        from FlagEmbedding import FlagReranker

        self.device = get_device()
        fp16 = use_fp16(self.device)
        try:
            self.model = FlagReranker(settings.rerank_model, use_fp16=fp16, devices=self.device)
        except TypeError:  # older FlagEmbedding
            self.model = FlagReranker(settings.rerank_model, use_fp16=fp16, device=self.device)
        self._lock = threading.Lock()

    def score(self, pairs: list[tuple[str, str]], batch_size: int = 32) -> list[float]:
        """Scores in 0..1 (sigmoid-normalized), one per (query, document) pair."""
        if not pairs:
            return []
        with self._lock:
            out = self.model.compute_score([list(p) for p in pairs], batch_size=batch_size,
                                           max_length=MAX_LENGTH, normalize=True)
        if not isinstance(out, (list, tuple)):  # a single pair returns a bare float
            out = [out]
        return [float(x) for x in out]


@lru_cache(maxsize=1)
def get_reranker() -> Reranker:
    return Reranker()
