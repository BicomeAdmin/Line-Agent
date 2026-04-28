"""Sentence-embedding service — semantic similarity for Project Echo.

Replaces the bigram-overlap heuristic in reply_target_selector with
real semantic comparison. The bigram approach couldn't see that
「股票漲了不少」 is related to 「台股 4 萬點要保守看」 (no shared
2-gram), but BGE-small-zh-v1.5 correctly scores cosine 0.61.

Model: BAAI/bge-small-zh-v1.5
  - 95 MB on disk
  - 30-80 ms per sentence on Apple Silicon CPU
  - Outputs 512-dim normalized embeddings
  - Works on both 繁體 and 簡體 Chinese (CKIP / Alibaba both verified)
  - MIT license, no API key required (downloads from HuggingFace once)

Singleton pattern — load once per process, reuse for every call.
First load is ~3-5 seconds (cold start cost is paid once at daemon
startup, not per scoring run).

Graceful degradation:
  - If sentence-transformers isn't installed, get_embedding_service()
    returns None and callers MUST fall back to bigram scoring.
  - If model load fails (no network on first run, disk full, etc.),
    same: returns None.
  - Tests can call set_test_service() to inject a stub without
    pulling the real 95 MB model.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Sequence


_LOCK = threading.Lock()
_INSTANCE: "EmbeddingService | None" = None
_INIT_FAILED = False
_TEST_OVERRIDE: "EmbeddingService | None" = None


# Default model — small + Chinese. Override via env if you need a
# different one (e.g. multilingual paraphrase for cross-language groups).
DEFAULT_MODEL = os.getenv("ECHO_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")


class EmbeddingService:
    """Wraps a SentenceTransformer model with caching for repeated
    encodes of the same text. Per-process LRU since chat tails repeat
    the operator's recent posts on every watcher tick."""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        from sentence_transformers import SentenceTransformer  # type: ignore
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._cache: dict[str, "object"] = {}  # text -> tensor
        self._cache_max = 1024

    def encode(self, text: str):
        """Return normalized embedding for one text. Cached."""
        if text in self._cache:
            return self._cache[text]
        emb = self._model.encode(text, normalize_embeddings=True, show_progress_bar=False)
        if len(self._cache) >= self._cache_max:
            # Drop oldest 10% (FIFO is fine for our access pattern).
            keys = list(self._cache.keys())[: self._cache_max // 10]
            for k in keys:
                self._cache.pop(k, None)
        self._cache[text] = emb
        return emb

    def cosine(self, text_a: str, text_b: str) -> float:
        """Cosine similarity in [-1, 1]; for normalized embeddings the
        dot product equals cosine. Returns 0.0 for empty inputs."""
        if not text_a or not text_b:
            return 0.0
        ea = self.encode(text_a)
        eb = self.encode(text_b)
        from sentence_transformers import util  # type: ignore
        return float(util.cos_sim(ea, eb))

    def max_similarity(self, query: str, corpus: Sequence[str]) -> float:
        """Highest cosine between query and any item in corpus.
        Used as the topic-overlap signal: how strongly does this
        message connect to anything the operator has said?"""
        if not query or not corpus:
            return 0.0
        scores = [self.cosine(query, c) for c in corpus if c]
        return max(scores) if scores else 0.0


def get_embedding_service() -> "EmbeddingService | None":
    """Return the process-wide singleton, or None if unavailable.

    Callers MUST handle None — either fall back to bigram scoring,
    or skip the topic-overlap signal entirely. This lets tests run
    without dragging in 95 MB of model weights, and lets the daemon
    start even if the user hasn't installed sentence-transformers.
    """

    global _INSTANCE, _INIT_FAILED

    if _TEST_OVERRIDE is not None:
        return _TEST_OVERRIDE
    if _INSTANCE is not None:
        return _INSTANCE
    if _INIT_FAILED:
        return None

    with _LOCK:
        if _INSTANCE is not None:
            return _INSTANCE
        if _INIT_FAILED:
            return None
        try:
            t0 = time.time()
            _INSTANCE = EmbeddingService()
            elapsed = time.time() - t0
            print(f"[embedding] loaded {_INSTANCE.model_name} in {elapsed:.1f}s", flush=True)
        except ImportError:
            _INIT_FAILED = True
            print("[embedding] sentence-transformers not installed; semantic scoring disabled", flush=True)
            return None
        except Exception as exc:  # noqa: BLE001
            _INIT_FAILED = True
            print(f"[embedding] init failed: {exc!r}; semantic scoring disabled", flush=True)
            return None
    return _INSTANCE


def set_test_service(stub: "EmbeddingService | None") -> None:
    """Inject a stub for unit tests. Pass None to clear."""

    global _TEST_OVERRIDE
    _TEST_OVERRIDE = stub
