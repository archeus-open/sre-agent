"""Text chunking for RAG ingestion."""

from __future__ import annotations


def chunk_text(text: str, chunk_size: int = 800, chunk_overlap: int = 120) -> list[str]:
    """Split text into overlapping chunks, preferring paragraph boundaries."""
    text = " ".join(text.split())  # normalize whitespace
    if len(text) <= chunk_size:
        return [text] if text else []

    chunks: list[str] = []
    start = 0
    step = max(1, chunk_size - chunk_overlap)
    while start < len(text):
        end = min(len(text), start + chunk_size)
        # try to break on a sentence/word boundary near the end
        if end < len(text):
            for sep in (". ", "\n", " "):
                cut = text.rfind(sep, start + chunk_size - 200, end)
                if cut > start:
                    end = cut + len(sep)
                    break
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start += step
    return chunks
