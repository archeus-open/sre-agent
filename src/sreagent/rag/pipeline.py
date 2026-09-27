"""RAG pipeline: ingest documents -> chunk -> embed -> vector store -> retrieve."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .chunk import chunk_text
from .embed import BaseEmbedder, get_embedder
from .store import ScoredChunk, create_store


class RAGPipeline:
    def __init__(
        self,
        embedder: BaseEmbedder | None = None,
        store_kind: str = "numpy",
        chunk_size: int = 800,
        chunk_overlap: int = 120,
    ) -> None:
        self.embedder = embedder or get_embedder("hashing")
        self.store = create_store(store_kind, self.embedder.dim)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # -- ingestion ----------------------------------------------------
    def ingest_texts(self, docs: list[dict[str, Any]]) -> int:
        """docs: [{"text": ..., "source": ...}]. Returns chunk count."""
        texts, metas = [], []
        for doc in docs:
            for chunk in chunk_text(doc["text"], self.chunk_size, self.chunk_overlap):
                texts.append(chunk)
                metas.append({"source": doc.get("source", "unknown")})
        if not texts:
            return 0
        self.store.add(self.embedder.embed(texts), texts, metas)
        return len(texts)

    def ingest_dir(self, path: str | Path, extensions: tuple[str, ...] = (".md", ".txt")) -> int:
        docs = []
        for file in sorted(Path(path).rglob("*")):
            if file.suffix.lower() in extensions and file.is_file():
                docs.append({"text": file.read_text(encoding="utf-8", errors="ignore"), "source": str(file)})
        return self.ingest_texts(docs)

    # -- retrieval ----------------------------------------------------
    def query(self, question: str, top_k: int = 5) -> list[ScoredChunk]:
        qv = self.embedder.embed([question])[0]
        return self.store.search(qv, top_k=top_k)

    def query_context(self, question: str, top_k: int = 5) -> str:
        hits = self.query(question, top_k=top_k)
        if not hits:
            return ""
        parts = [
            f"[doc {i+1} | {h.meta.get('source', '?')} | score={h.score:.2f}]\n{h.text}"
            for i, h in enumerate(hits)
        ]
        return "\n\n".join(parts)

    def __len__(self) -> int:
        return len(self.store)
