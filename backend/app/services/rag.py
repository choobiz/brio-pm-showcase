"""Semantic retrieval (RAG) over project documents — the "Keystone" knowledge layer.

Pipeline: chunk → embed → store → retrieve top-k by cosine similarity →
answer with Claude, grounded and cited.

The **embedder** and **vector store** are injected behind Protocols, so the
retrieval/ranking logic is unit-tested offline with a deterministic fake
embedder + in-memory store (no API key, no DB), while production swaps in
Voyage embeddings + a pgvector-backed store *without touching the logic*.

Production store schema (pgvector — provisioned by migration, not create_all):

    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE document_chunks (
        id          BIGSERIAL PRIMARY KEY,
        document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        project_id  UUID,
        ordinal     INT  NOT NULL,
        text        TEXT NOT NULL,
        embedding   vector(1024) NOT NULL          -- Voyage voyage-3 dim
    );
    CREATE INDEX ON document_chunks USING ivfflat (embedding vector_cosine_ops);
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence


# ── chunking ──────────────────────────────────────────────────────────

@dataclass
class Chunk:
    document_id: str
    ordinal: int
    text: str
    project_id: str | None = None


def chunk_text(
    text: str,
    document_id: str,
    *,
    max_chars: int = 1200,
    overlap: int = 150,
    project_id: str | None = None,
) -> list[Chunk]:
    """Split on paragraph boundaries, packing up to `max_chars` per chunk with
    a trailing `overlap` carried into the next chunk (keeps a sentence that
    straddles a boundary retrievable from both sides). Deterministic."""
    if overlap >= max_chars:
        raise ValueError("overlap must be < max_chars")
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[Chunk] = []
    buf = ""
    for para in paras:
        if buf and len(buf) + 1 + len(para) > max_chars:
            chunks.append(buf)
            buf = (buf[-overlap:] + "\n" + para) if overlap else para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf.strip():
        chunks.append(buf)
    return [
        Chunk(document_id=document_id, ordinal=i, text=c.strip(), project_id=project_id)
        for i, c in enumerate(chunks)
    ]


# ── embedder + store seams ────────────────────────────────────────────

class Embedder(Protocol):
    dim: int
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None: ...
    def search(self, query_embedding: Sequence[float], k: int,
               project_id: str | None = None) -> list[tuple[Chunk, float]]: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class InMemoryVectorStore:
    """Cosine search in Python. For tests, small corpora, and eval."""
    _rows: list[tuple[Chunk, list[float]]] = field(default_factory=list)

    def upsert(self, chunks, embeddings) -> None:
        for c, e in zip(chunks, embeddings):
            self._rows.append((c, list(e)))

    def search(self, query_embedding, k, project_id=None):
        rows = [(c, e) for (c, e) in self._rows if project_id is None or c.project_id == project_id]
        scored = [(c, cosine(query_embedding, e)) for c, e in rows]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]


class PgVectorStore:
    """Production store — pgvector cosine distance (`<=>`) over `document_chunks`.
    `conn` is any object exposing SQLAlchemy-style `.execute(text, params)`.
    Kept import-light: no ORM model needed (raw SQL, matching this repo's style)."""

    def __init__(self, conn: Any):
        self.conn = conn

    def upsert(self, chunks, embeddings) -> None:
        from sqlalchemy import text
        for c, e in zip(chunks, embeddings):
            self.conn.execute(
                text("""INSERT INTO document_chunks
                        (document_id, project_id, ordinal, text, embedding)
                        VALUES (:d, :p, :o, :t, :emb)"""),
                {"d": c.document_id, "p": c.project_id, "o": c.ordinal,
                 "t": c.text, "emb": _to_pgvector(e)},
            )

    def search(self, query_embedding, k, project_id=None):
        from sqlalchemy import text
        rows = self.conn.execute(
            text("""SELECT document_id, project_id, ordinal, text,
                           1 - (embedding <=> :q) AS score
                    FROM document_chunks
                    WHERE (:pid IS NULL OR project_id = :pid)
                    ORDER BY embedding <=> :q
                    LIMIT :k"""),
            {"q": _to_pgvector(query_embedding), "pid": project_id, "k": k},
        ).mappings().all()
        return [
            (Chunk(document_id=str(r["document_id"]), ordinal=r["ordinal"],
                   text=r["text"], project_id=str(r["project_id"]) if r["project_id"] else None),
             float(r["score"]))
            for r in rows
        ]


def _to_pgvector(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


# ── production embedder (lazy — Voyage is Anthropic's embeddings partner) ──

class VoyageEmbedder:
    dim = 1024

    def __init__(self, api_key: str | None = None, model: str = "voyage-3"):
        self._key = api_key
        self._model = model

    def embed(self, texts):
        import voyageai  # lazy: optional dep, only needed for live ingest
        client = voyageai.Client(api_key=self._key)
        return client.embed(list(texts), model=self._model, input_type="document").embeddings


# ── retriever + answerer ──────────────────────────────────────────────

@dataclass
class Retriever:
    embedder: Embedder
    store: VectorStore

    def ingest(self, document_id: str, text: str, *, project_id: str | None = None) -> int:
        chunks = chunk_text(text, document_id, project_id=project_id)
        if not chunks:
            return 0
        embeddings = self.embedder.embed([c.text for c in chunks])
        self.store.upsert(chunks, embeddings)
        return len(chunks)

    def retrieve(self, query: str, k: int = 5, project_id: str | None = None):
        q_emb = self.embedder.embed([query])[0]
        return self.store.search(q_emb, k, project_id=project_id)


@dataclass
class RagAnswer:
    answer: str
    sources: list[Chunk]
    cost_usd: float = 0.0
    latency_ms: int = 0


def build_grounded_prompt(query: str, hits: Sequence[tuple[Chunk, float]]) -> tuple[str, str]:
    """System + user for a cited, grounded answer. Numbered sources → the model
    is told to answer ONLY from them and cite as [n], or say it doesn't know."""
    system = (
        "You are Keystone, BRIO Construction's project assistant. Answer the question "
        "using ONLY the numbered sources. Cite the sources you use as [n]. If the "
        "sources don't contain the answer, say you don't have that information — "
        "never invent facts."
    )
    src_block = "\n\n".join(
        f"[{i+1}] (doc {c.document_id}) {c.text}" for i, (c, _s) in enumerate(hits)
    )
    user = f"Question: {query}\n\nSources:\n{src_block}"
    return system, user


def answer(
    retriever: Retriever,
    query: str,
    *,
    k: int = 5,
    project_id: str | None = None,
    call_fn: Callable[[str, list[dict[str, Any]]], Any] | None = None,
) -> RagAnswer:
    """Retrieve then generate. `call_fn(system, messages)` is injected — pass the
    production `call_anthropic` (wrapped) in prod, a stub in tests."""
    hits = retriever.retrieve(query, k=k, project_id=project_id)
    system, user = build_grounded_prompt(query, hits)
    sources = [c for c, _ in hits]
    if call_fn is None:  # retrieval-only mode (e.g. eval of the retriever alone)
        return RagAnswer(answer="", sources=sources)
    res = call_fn(system, [{"role": "user", "content": user}])
    text = getattr(res, "text", res if isinstance(res, str) else "")
    return RagAnswer(
        answer=text, sources=sources,
        cost_usd=float(getattr(res, "cost_usd", 0.0) or 0.0),
        latency_ms=int(getattr(res, "latency_ms", 0) or 0),
    )
