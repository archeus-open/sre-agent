"""Embedders: turn text into dense vectors.

- ``HashingEmbedder``: zero-dependency, deterministic char-trigram hashing.
  Good enough for prototypes, tests, and offline demos.
- ``SentenceTransformerEmbedder``: quality embeddings when the
  [embeddings] extra is installed.
"""

from __future__ import annotations

import hashlib
import math
import re


class BaseEmbedder:
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbedder(BaseEmbedder):
    """Deterministic trigram-hash embeddings, L2-normalized."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _WORD.findall(text.lower())
        # word unigrams + char trigrams for a bit of fuzzy matching
        feats = tokens + [
            text.lower()[i : i + 3] for i in range(max(0, len(text.lower()) - 2))
        ]
        for feat in feats:
            h = int(hashlib.md5(feat.encode()).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class SentenceTransformerEmbedder(BaseEmbedder):
    """Production-quality embeddings via sentence-transformers."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. Install it with: pip install 'finagent[embeddings]'"
            ) from exc
        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()


def get_embedder(kind: str = "hashing", **kwargs) -> BaseEmbedder:
    if kind == "sentence-transformers":
        return SentenceTransformerEmbedder(**kwargs)
    return HashingEmbedder(**kwargs)
