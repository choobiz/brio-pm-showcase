"""Tests for the RAG retrieval layer (app/services/rag).

Fully offline: a deterministic feature-hashing fake embedder + the in-memory
vector store. No API key, no DB, no pgvector. Proves chunking, cosine ranking,
project scoping, and answer/citation assembly.
"""

from __future__ import annotations

import hashlib
import re

from app.services.rag import (
    Chunk, chunk_text, cosine, InMemoryVectorStore, Retriever, answer, build_grounded_prompt,
)


class FakeEmbedder:
    """Feature-hashing bag-of-words. Deterministic (hashlib, not builtin hash()).
    Texts that share tokens get higher cosine — enough to test ranking."""
    dim = 64

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in re.findall(r"[a-z]{3,}", t.lower()):
                b = int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim
                v[b] += 1.0
            out.append(v)
        return out


def _retriever():
    return Retriever(embedder=FakeEmbedder(), store=InMemoryVectorStore())


def test_chunk_text_packs_and_overlaps():
    text = "\n\n".join([f"Paragraph number {i} with some filler words here." for i in range(10)])
    chunks = chunk_text(text, "doc-1", max_chars=120, overlap=20)
    assert len(chunks) > 1
    assert all(isinstance(c, Chunk) for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    # every chunk stays within a sane bound of max_chars (+ one overlap + para)
    assert all(len(c.text) <= 300 for c in chunks)


def test_cosine_bounds():
    assert cosine([1, 0, 0], [1, 0, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([1, 0], [0, 0]) == 0.0  # zero vector → 0, no div error


def test_retrieve_ranks_relevant_chunk_first():
    r = _retriever()
    r.ingest("permit", "City of Vancouver building permit issued. Permit expiry date noted. Occupancy R1.")
    r.ingest("invoice", "Progress invoice amount due net fifteen. Remit payment to contractor.")
    r.ingest("contract", "Fixed price construction contract. Warranty one year. Completion schedule.")

    hits = r.retrieve("when does the building permit expire", k=3)
    assert hits, "expected retrieval results"
    top_doc = hits[0][0].document_id
    assert top_doc == "permit", f"expected permit chunk on top, got {top_doc}"
    assert hits[0][1] > 0  # positive similarity


def test_project_scoping_filters():
    r = _retriever()
    r.ingest("a", "shared kitchen renovation scope tile paint", project_id="P1")
    r.ingest("b", "shared kitchen renovation scope tile paint", project_id="P2")
    hits = r.retrieve("kitchen renovation", k=5, project_id="P2")
    assert hits and all(c.project_id == "P2" for c, _ in hits)


def test_ingest_returns_chunk_count():
    r = _retriever()
    n = r.ingest("d", "\n\n".join(f"para {i} words words words" for i in range(6)), )
    assert n >= 1
    # retriever now has content
    assert r.retrieve("para words", k=2)


def test_answer_assembles_cited_sources():
    r = _retriever()
    r.ingest("permit", "Permit BP-2026-1 issued; expiry 2027-01-01; status ISSUED.")
    r.ingest("noise", "Unrelated note about parking.")

    def stub_call(system, messages):
        # a well-behaved model would cite [1]; assert our prompt gave it sources
        assert "Sources:" in messages[0]["content"]
        assert "ONLY the numbered sources" in system

        class _R:
            text = "The permit expires 2027-01-01 [1]."
            cost_usd = 0.002
            latency_ms = 420
        return _R()

    out = answer(r, "when does the permit expire?", k=2, call_fn=stub_call)
    assert "[1]" in out.answer
    # plumbing test: the permit chunk is retrieved + carried as a citable source
    # (top-1 ranking precision is covered by test_retrieve_ranks_relevant_chunk_first)
    assert any(s.document_id == "permit" for s in out.sources)
    assert out.cost_usd == 0.002 and out.latency_ms == 420


def test_answer_retrieval_only_when_no_call_fn():
    r = _retriever()
    r.ingest("x", "some project content about drywall and framing")
    out = answer(r, "framing", k=1)  # no call_fn → retrieval only
    assert out.answer == "" and out.sources


def test_build_grounded_prompt_numbers_sources():
    hits = [(Chunk("d1", 0, "alpha"), 0.9), (Chunk("d2", 0, "beta"), 0.8)]
    system, user = build_grounded_prompt("q?", hits)
    assert "[1]" in user and "[2]" in user and "alpha" in user and "beta" in user
