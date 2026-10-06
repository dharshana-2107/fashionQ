"""BGE-M3 embedder: one model, two kinds of vectors per text.

- dense  : 1024 numbers that capture the *meaning* of the text. Works across
           languages, so a Tamil query can land near an English product.
- sparse : {token_id: weight} for the *important words* in the text. Great for
           exact things like brand names ("crocs"), sizes ("XL"), "linen".

Loading the model takes ~10-20 s and ~2.5 GB RAM, so we load it ONCE per process
via get_embedder() and reuse it.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

import numpy as np

from common.config import settings
from common.gpu import GPU_LOCK
from common.device import get_device, use_fp16

DENSE_DIM = 1024        # BGE-M3 dense vector size
DOC_MAX_TOKENS = 1024   # our product documents are well under this
QUERY_MAX_TOKENS = 128  # queries are short


@dataclass
class SparseVec:
    indices: list[int]
    values: list[float]


@dataclass
class Embedding:
    dense: list[float]
    sparse: SparseVec


def _to_sparse(weights: dict) -> SparseVec:
    """FlagEmbedding gives {"6085": 0.21, ...}; Qdrant wants two parallel lists."""
    items = sorted((int(k), float(v)) for k, v in weights.items() if float(v) > 0)
    return SparseVec(indices=[i for i, _ in items], values=[v for _, v in items])


class Embedder:
    def __init__(self, model_name: str | None = None, device: str | None = None):
        hf_home = getattr(settings, "hf_home", None)
        if hf_home:
            os.environ.setdefault("HF_HOME", str(hf_home))
        from FlagEmbedding import BGEM3FlagModel  # heavy import, so do it lazily

        self.model_name = model_name or settings.embed_model
        self.device = device or get_device()
        fp16 = use_fp16(self.device)
        try:  # FlagEmbedding >= 1.3
            self.model = BGEM3FlagModel(self.model_name, use_fp16=fp16, devices=self.device)
        except TypeError:  # older FlagEmbedding used `device=`
            self.model = BGEM3FlagModel(self.model_name, use_fp16=fp16, device=self.device)
        self._lock = GPU_LOCK  # shared with every other model: MPS is not thread-safe

    def encode(self, texts: Sequence[str], batch_size: int = 16,
               max_length: int = DOC_MAX_TOKENS) -> list[Embedding]:
        if not texts:
            return []
        with self._lock:
            out = self.model.encode(
                list(texts), batch_size=batch_size, max_length=max_length,
                return_dense=True, return_sparse=True, return_colbert_vecs=False,
            )
        dense = np.asarray(out["dense_vecs"], dtype=np.float32)
        return [Embedding(dense=d.tolist(), sparse=_to_sparse(w))
                for d, w in zip(dense, out["lexical_weights"])]

    def encode_documents(self, texts: Sequence[str], batch_size: int = 16) -> list[Embedding]:
        return self.encode(texts, batch_size=batch_size, max_length=DOC_MAX_TOKENS)

    def encode_query(self, text: str) -> Embedding:
        # BGE-M3 needs no "query:" prefix, unlike some other embedding models.
        return self.encode([text], batch_size=1, max_length=QUERY_MAX_TOKENS)[0]


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()
