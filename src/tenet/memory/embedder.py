"""Embedders turn text into vectors. The embedding is a *derived projection* of
canonical content — swappable, never authoritative.

`Embedder` is the interface. Two implementations ship:

- `HashingEmbedder` (default): deterministic, no model download, no heavy deps.
  Good enough to demonstrate retrieval ranking and namespace filtering offline.
  It captures lexical overlap, NOT deep semantic similarity.

- `SentenceTransformerEmbedder`: real semantic embeddings. One-line swap for
  production. Requires `pip install sentence-transformers` and a model download
  on first use.

Nothing else in Tenet knows or cares which one is wired in. The contextualizer,
retriever, and core all depend on `Embedder`, never on a concrete model.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

_TOKEN = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    dim: int
    identity: str

    def embed(self, text: str) -> list[float]:
        ...


class HashingEmbedder:
    """Hash tokens into a fixed-dimensional bag-of-words vector, L2-normalised.

    Deterministic and dependency-light. Two texts that share words land near
    each other; unrelated texts do not. Enough to show the architecture working
    end to end without downloading anything.
    """

    def __init__(self, dim: int = 256) -> None:
        if type(dim) is not int or dim <= 0:
            raise ValueError('embedding dimension must be positive')
        self.dim = dim
        self.identity = f'hashing/sha1-ascii-token-l2/v1/dim={dim}'

    def _bucket(self, token: str) -> int:
        h = hashlib.sha1(token.encode("utf-8")).digest()
        return int.from_bytes(h[:4], "big") % self.dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN.findall(text.lower()):
            vec[self._bucket(token)] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class SentenceTransformerEmbedder:
    """Real semantic embeddings via sentence-transformers.

    Swap this in for `HashingEmbedder` when you want true semantic similarity.
    Nothing else changes.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", *, revision: str) -> None:
        if not isinstance(revision, str) or re.fullmatch(r"[a-fA-F0-9]{40}", revision) is None:
            raise ValueError("revision must be an immutable 40-character model commit")
        try:
            from sentence_transformers import SentenceTransformer
            from importlib.metadata import version
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "SentenceTransformerEmbedder needs sentence-transformers:\n"
                "    pip install sentence-transformers"
            ) from exc
        self._model = SentenceTransformer(model_name, revision=revision)
        self.dim = self._model.get_sentence_embedding_dimension()
        self.identity = (f'sentence-transformers/{model_name}@{revision}/normalized/dim={self.dim}'
                         f'/sentence-transformers={version("sentence-transformers")}'
                         f'/transformers={version("transformers")}/torch={version("torch")}')

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()
