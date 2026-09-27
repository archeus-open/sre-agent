"""Vector stores: cosine-similarity search over chunk embeddings.

- ``NumpyVectorStore``: pure-numpy brute force. Zero extra deps, fine for
  prototype-scale corpora (thousands of chunks).
- ``FaissVectorStore``: FAISS inner-product index, used automatically when
  faiss is installed and requested.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoredChunk:
    score: float
    text: str
    meta: dict = field(default_factory=dict)


class NumpyVectorStore:
    def __init__(self, dim: int) -> None:
        import numpy as np

        self._np = np
        self.dim = dim
        self._vectors = np.zeros((0, dim), dtype="float32")
        self._texts: list[str] = []
        self._metas: list[dict] = []

    def add(self, vectors: list[list[float]], texts: list[str], metas: list[dict] | None = None) -> None:
        import numpy as np

        arr = np.asarray(vectors, dtype="float32")
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms
        self._vectors = np.vstack([self._vectors, arr]) if len(self._texts) else arr
        self._texts.extend(texts)
        self._metas.extend(metas or [{}] * len(texts))

    def search(self, query_vector: list[float], top_k: int = 5) -> list[ScoredChunk]:
        import numpy as np

        if not self._texts:
            return []
        q = np.asarray(query_vector, dtype="float32")
        q = q / (np.linalg.norm(q) or 1.0)
        scores = self._vectors @ q
        idx = np.argsort(scores)[::-1][:top_k]
        return [
            ScoredChunk(float(scores[i]), self._texts[i], self._metas[i]) for i in idx
        ]

    def __len__(self) -> int:
        return len(self._texts)


class FaissVectorStore:
    def __init__(self, dim: int) -> None:
        try:
            import faiss  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "faiss is not installed. Install it with: pip install 'finagent[vector]'"
            ) from exc
        self._faiss = faiss
        self.dim = dim
        self.index = faiss.IndexFlatIP(dim)
        self._texts: list[str] = []
        self._metas: list[dict] = []

    def add(self, vectors: list[list[float]], texts: list[str], metas: list[dict] | None = None) -> None:
        import numpy as np

        arr = np.asarray(vectors, dtype="float32")
        self._faiss.normalize_L2(arr)
        self.index.add(arr)
        self._texts.extend(texts)
        self._metas.extend(metas or [{}] * len(texts))

    def search(self, query_vector: list[float], top_k: int = 5) -> list[ScoredChunk]:
        import numpy as np

        if not self._texts:
            return []
        q = np.asarray([query_vector], dtype="float32")
        self._faiss.normalize_L2(q)
        scores, idx = self.index.search(q, min(top_k, len(self._texts)))
        return [
            ScoredChunk(float(scores[0][j]), self._texts[i], self._metas[i])
            for j, i in enumerate(idx[0]) if i >= 0
        ]

    def __len__(self) -> int:
        return len(self._texts)


def create_store(kind: str, dim: int):
    if kind == "faiss":
        return FaissVectorStore(dim)
    return NumpyVectorStore(dim)
