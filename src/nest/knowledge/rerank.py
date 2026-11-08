"""Reranking: a cross-encoder reads the question together with each candidate section and scores how well
the section answers it (0 to 1). Served locally by llama.cpp's llama-server --reranking (/v1/rerank).
Slower than vector search, so it only orders the few dozen candidates the other searches found; its score
also decides whether the docs answer the question at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

TIMEOUT = 60.0


@dataclass(frozen=True)
class RerankModel:
    """`logits`: the server returns raw scores (bge-reranker does), turned into 0..1 here; Qwen3-Reranker
    already returns 0..1."""

    name: str
    url: str  # http://127.0.0.1:3063
    logits: bool = False


class RerankClient:
    def __init__(self, model: RerankModel, client: httpx.AsyncClient | None = None) -> None:
        self.model = model
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT, trust_env=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def scores(self, query: str, documents: list[str]) -> list[float]:
        """How well each document answers `query`, in the documents' order."""
        if not documents:
            return []
        response = await self._client.post(
            f"{self.model.url.rstrip('/')}/v1/rerank",
            json={"model": self.model.name, "query": query, "documents": documents, "top_n": len(documents)},
        )
        response.raise_for_status()
        out = [0.0] * len(documents)
        for row in response.json()["results"]:
            score = float(row["relevance_score"])
            out[row["index"]] = 1 / (1 + math.exp(-score)) if self.model.logits else score
        return out
