"""RAG package: chunking, embeddings, vector stores, retrieval pipeline."""

from .chunk import chunk_text
from .embed import BaseEmbedder, HashingEmbedder, SentenceTransformerEmbedder, get_embedder
from .pipeline import RAGPipeline
from .store import FaissVectorStore, NumpyVectorStore, ScoredChunk, create_store

__all__ = [
    "chunk_text",
    "BaseEmbedder",
    "HashingEmbedder",
    "SentenceTransformerEmbedder",
    "get_embedder",
    "RAGPipeline",
    "FaissVectorStore",
    "NumpyVectorStore",
    "ScoredChunk",
    "create_store",
]
