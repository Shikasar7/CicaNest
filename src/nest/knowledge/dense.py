"""Vector search over help sections, with an embedding model served locally (llama.cpp's llama-server
--embedding, OpenAI-compatible /v1/embeddings).

A few hundred sections fit in memory, so every search compares the question with all of them: no
vector database. Section vectors are kept by the hash of what was embedded and the model's name, so only
new or changed sections are embedded again.
"""

from __future__ import annotations

import hashlib
import math
from array import array
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import httpx

from nest.knowledge.loader import Section

BATCH = 16
TIMEOUT = 60.0


@dataclass(frozen=True)
class EmbeddingModel:
    """An embedding server and how this model wants its text. `query_prefix`: put before a question
    (Qwen3-Embedding wants an instruction there; bge-m3 wants nothing)."""

    name: str
    url: str  # http://127.0.0.1:3061
    query_prefix: str = ""


def document_text(section: Section, *, header: bool = True) -> str:
    """What is embedded (and reranked) for a section: its one-line header (what it is about, where it is
    in the app) and the section itself."""
    return f"{section.header()}\n{section.text()}" if header else section.text()


def text_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def _unit(vector: Iterable[float]) -> array:
    values = array("f", vector)
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return array("f", (v / norm for v in values))


class EmbeddingClient:
    def __init__(self, model: EmbeddingModel, client: httpx.AsyncClient | None = None) -> None:
        self.model = model
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT, trust_env=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, texts: list[str]) -> list[array]:
        """Unit-length vectors for `texts`, in order."""
        out: list[array] = []
        for start in range(0, len(texts), BATCH):
            batch = texts[start : start + BATCH]
            response = await self._client.post(
                f"{self.model.url.rstrip('/')}/v1/embeddings", json={"input": batch, "model": self.model.name}
            )
            response.raise_for_status()
            rows = sorted(response.json()["data"], key=lambda row: row["index"])
            out += [_unit(row["embedding"]) for row in rows]
        return out

    async def embed_query(self, question: str) -> array:
        return (await self.embed([self.model.query_prefix + question]))[0]


class DenseIndex:
    """Section vectors in memory. `cache`: earlier vectors by text_key (filled with the new ones)."""

    def __init__(self, sections: list[Section], vectors: list[array]) -> None:
        self.sections = sections
        self.vectors = vectors

    @classmethod
    async def build(
        cls,
        sections: Iterable[Section],
        client: EmbeddingClient,
        *,
        header: bool = True,
        cache: dict[str, array] | None = None,
    ) -> DenseIndex:
        sections = list(sections)
        cache = {} if cache is None else cache
        keys = [text_key(client.model.name, document_text(s, header=header)) for s in sections]
        missing = [i for i, key in enumerate(keys) if key not in cache]
        if missing:
            vectors = await client.embed([document_text(sections[i], header=header) for i in missing])
            for i, vector in zip(missing, vectors, strict=True):
                cache[keys[i]] = vector
        return cls(sections, [cache[key] for key in keys])

    def search(self, query: array, k: int = 30) -> list[tuple[Section, float]]:
        """The `k` sections closest to `query` (cosine similarity, both unit length), closest first."""
        scored = sorted(
            ((sum(a * b for a, b in zip(query, v, strict=True)), i) for i, v in enumerate(self.vectors)),
            reverse=True,
        )
        return [(self.sections[i], score) for score, i in scored[:k]]


def cache_from(rows: Mapping[str, list[float]]) -> dict[str, array]:
    return {key: array("f", values) for key, values in rows.items()}
