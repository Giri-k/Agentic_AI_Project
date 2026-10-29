"""Retrieval without network: a fake embedder stands in for Portkey so these tests are free and repeatable."""

import hashlib
import shutil

import numpy as np
import pytest

from agent.config import KB_MAX_CHARS, POLICY_DIR
from agent.dispatcher import Dispatcher
from agent.retrieval import Retriever, load_policies, make_chunks
from conftest import args


def fake_embed(texts):
    """Bag-of-words vectors: each word adds 1 to a hashed slot. Similar wording -> similar vectors."""
    out = []
    for text in texts:
        v = np.zeros(256)
        for word in text.lower().split():
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 256] += 1
        out.append((v + 1e-6).tolist())
    return out


@pytest.fixture
def retriever(tmp_path):
    """A retriever with the fake embedder and its own vector cache in a temp folder."""
    return Retriever(embed_fn=fake_embed, cache_path=tmp_path / "kb_index.json")


def test_every_policy_has_four_sections_and_becomes_four_chunks():
    policies = load_policies()
    assert len(policies) == 20 and all(set(p.sections) == {"S1", "S2", "S3", "S4"} for p in policies)
    assert len(make_chunks(policies)) == 80


def test_passages_keep_conditions_and_drop_scope():
    ret01 = next(p for p in load_policies() if p.id == "POL-RET-01")
    text = ret01.passage()
    assert "30 calendar days" in text and "Scope:" not in text and text.startswith("POL-RET-01 v")


def test_search_returns_distinct_policies_within_the_character_limit(retriever):
    found = retriever.search("refund return received approval balance final sale gift warranty", k=3)
    ids = [p["policy_id"] for p in found["passages"]]
    assert found["method"] == "embeddings" and len(ids) == len(set(ids)) == 3
    assert found["chars"] <= KB_MAX_CHARS


def test_embedding_outage_falls_back_to_bm25_and_says_why(retriever):
    retriever.embedding_outages = 1
    first = retriever.search("which cards do you accept")
    second = retriever.search("which cards do you accept")
    assert first["method"] == "bm25" and "outage" in first["fallback_reason"]
    assert second["method"] == "embeddings"


def test_vectors_are_cached_and_only_changed_sections_are_re_embedded(tmp_path):
    cache = tmp_path / "kb_index.json"
    assert Retriever(embed_fn=fake_embed, cache_path=cache).embeddings.newly_embedded == 80
    assert Retriever(embed_fn=fake_embed, cache_path=cache).embeddings.newly_embedded == 0

    policy_dir = tmp_path / "policies"
    shutil.copytree(POLICY_DIR, policy_dir)
    edited = policy_dir / "POL-SHIP-02.md"
    edited.write_text(edited.read_text().replace("$4.99 per order", "$5.99 per order"))
    assert Retriever(embed_fn=fake_embed, policy_dir=policy_dir, cache_path=cache).embeddings.newly_embedded == 1


def test_kb_lookup_through_the_dispatcher_uses_bm25_on_outage_tickets(store, retriever):
    outage_ticket = next(t for t in store.tickets if (store.failure_injection(t) or {}).get("service") == "embeddings")
    d = Dispatcher(store, outage_ticket, retriever=retriever)
    result = d.call("kb_lookup", args(query="where is my package", k=3))
    assert result["ok"] and result["result"]["method"] == "bm25"
