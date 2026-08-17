from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer

from recallzero.intelligence.nim_client import NIMClient, NIMError, NIMTransientError
from recallzero.models import EmbeddingMethod

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EmbeddingResult:
    matrix: np.ndarray | sparse.spmatrix
    method: EmbeddingMethod
    model_name: str | None = None


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> EmbeddingResult: ...


class TFIDFEmbedder:
    def __init__(self, max_features: int = 4096):
        self.max_features = max_features

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(matrix=np.empty((0, 0)), method=EmbeddingMethod.TFIDF)
        vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            min_df=1,
            max_df=1.0,
            max_features=self.max_features,
            sublinear_tf=True,
            strip_accents="unicode",
        )
        matrix = vectorizer.fit_transform(texts)
        return EmbeddingResult(matrix=matrix, method=EmbeddingMethod.TFIDF, model_name="tfidf")


class NIMEmbedder:
    def __init__(self, client: NIMClient, model_name: str, batch_size: int = 64):
        self.client = client
        self.model_name = model_name
        self.batch_size = batch_size

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            vectors.extend(
                await self.client.embeddings(
                    model=self.model_name,
                    texts=batch,
                    input_type="passage",
                    truncate="END",
                )
            )
        matrix = np.asarray(vectors, dtype=np.float32)
        return EmbeddingResult(matrix=matrix, method=EmbeddingMethod.NIM, model_name=self.model_name)


class HybridEmbedder:
    def __init__(
        self,
        nim: NIMEmbedder | None,
        fallback: TFIDFEmbedder | None = None,
        fallback_on_transient_error: bool = False,
    ):
        self.nim = nim
        self.fallback = fallback or TFIDFEmbedder()
        self.fallback_on_transient_error = fallback_on_transient_error

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if self.nim is not None:
            try:
                return await self.nim.embed(texts)
            except NIMTransientError as exc:
                if not self.fallback_on_transient_error:
                    logger.error(
                        "Transient NIM embedding failure exhausted retries; refusing TF-IDF fallback to preserve run consistency: %s",
                        exc,
                    )
                    raise
                logger.warning("Transient NIM embedding failure; using explicitly enabled TF-IDF fallback: %s", exc)
            except (NIMError, ValueError) as exc:
                logger.warning("NIM embedding failed; using TF-IDF fallback: %s", exc)
        return await self.fallback.embed(texts)
