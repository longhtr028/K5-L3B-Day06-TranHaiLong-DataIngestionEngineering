"""Real local embedding model: intfloat/multilingual-e5-small via sentence-transformers.

E5 models are trained with asymmetric prefixes: documents as "passage: ...", queries as
"query: ...". Vectors are L2-normalized so cosine distance in pgvector is well-defined.
No fallback: if the model cannot load, the exception propagates.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

DEFAULT_MODEL = "intfloat/multilingual-e5-small"


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, question: str) -> list[float]: ...


class E5Embedder:
    doc_prefix = "passage: "
    query_prefix = "query: "

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "cpu", batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, keep lazy

        self.model_name = model_name
        self.model = SentenceTransformer(model_name, device=device)
        self.dimensions = int(self.model.get_embedding_dimension())
        self.max_tokens = int(self.model.max_seq_length)
        self.batch_size = batch_size
        self.revision = self._revision()

    def _revision(self) -> str:
        """HF snapshot hash of the loaded weights, for the report."""
        try:
            from huggingface_hub import try_to_load_from_cache

            path = try_to_load_from_cache(self.model_name, "config.json")
            return Path(path).parent.name if isinstance(path, str) else "unknown"
        except Exception:
            return "unknown"

    def token_counts(self, texts: list[str]) -> list[int]:
        tok = self.model.tokenizer
        return [len(tok(self.doc_prefix + t, add_special_tokens=True)["input_ids"]) for t in texts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vecs = self.model.encode([self.doc_prefix + t for t in texts], batch_size=self.batch_size,
                                 normalize_embeddings=True, show_progress_bar=False)
        return vecs.tolist()

    def embed_query(self, question: str) -> list[float]:
        return self.model.encode(self.query_prefix + question, normalize_embeddings=True).tolist()
