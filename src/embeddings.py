"""Semantic relevance gate using local embeddings (fastembed, no API key).

Sits between the lexicon filter and the optional LLM tier. Kills posts that
mention the keywords but are semantically about something else (e.g. "time
tracking" in r/Porsche = track days, not time-tracking software).

The product-description anchor vector is cached in the Apify key-value store,
keyed by a hash of the product text + model, so repeat runs skip recompute.
Fails open: if embeddings are unavailable, the gate passes everything (the
lexicon + optional LLM tiers still apply) rather than crashing a run.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from apify import Actor

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"

# Measured cosine on labeled posts: real leads 0.58-0.83, clearly off-topic
# 0.42, ambiguous (Porsche track days) 0.49. 0.52 rejects the obvious noise
# while keeping real leads. Tunable; the LLM tier handles adversarial near-misses.
REL_THRESHOLD = 0.52


def _anchor_text(product_description: str) -> str:
    return product_description.strip()


def _cache_key(product_description: str) -> str:
    h = hashlib.sha256(f"{MODEL_NAME}|{product_description}".encode()).hexdigest()[:16]
    return f"anchor-embedding-{h}"


class EmbeddingGate:
    """Loads the model once, holds the anchor vector, scores post relevance."""

    def __init__(self) -> None:
        self._model: Any = None
        self._anchor: list[float] | None = None
        self.enabled = False

    async def setup(self, product_description: str) -> None:
        """Load model + anchor vector (from KV cache if present). Fail-open."""
        try:
            import numpy as np  # noqa: F401
            from fastembed import TextEmbedding
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embeddings unavailable (%s); semantic gate disabled.", exc)
            return

        key = _cache_key(product_description)
        try:
            store = await Actor.open_key_value_store()
            cached = await store.get_value(key)
        except Exception:  # noqa: BLE001
            store = None
            cached = None

        try:
            self._model = TextEmbedding(MODEL_NAME)
            if isinstance(cached, list) and cached:
                self._anchor = cached
            else:
                self._anchor = list(next(iter(self._model.embed([_anchor_text(product_description)]))))
                self._anchor = [float(x) for x in self._anchor]
                if store is not None:
                    try:
                        await store.set_value(key, self._anchor)
                    except Exception:  # noqa: BLE001
                        pass
            self.enabled = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embedding setup failed (%s); semantic gate disabled.", exc)
            self.enabled = False

    def relevance(self, text: str) -> float | None:
        """Cosine similarity of `text` to the product anchor, or None if disabled."""
        if not self.enabled or self._anchor is None or not text.strip():
            return None
        import numpy as np

        vec = np.array(next(iter(self._model.embed([text]))), dtype=float)
        anchor = np.array(self._anchor, dtype=float)
        denom = np.linalg.norm(vec) * np.linalg.norm(anchor)
        if denom == 0:
            return None
        return float(np.dot(vec, anchor) / denom)

    def passes(self, text: str) -> bool:
        """True if the post is semantically relevant enough (or gate disabled)."""
        score = self.relevance(text)
        if score is None:
            return True  # fail-open
        return score >= REL_THRESHOLD
