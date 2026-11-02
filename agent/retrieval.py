"""Policy search behind kb_lookup.

Every policy section (Scope, Conditions, Rules, Exceptions) is indexed as its own chunk.
Policies are ranked by their best-matching section, and the top k distinct policies come
back as Conditions + Rules + Exceptions, at most 4,000 characters in total.
Embeddings + cosine similarity is the main method; BM25 is the fallback and the baseline.
"""

import hashlib
import json
import re
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi

from .config import EMBED_MODEL, KB_MAX_CHARS, KB_MAX_K, POLICY_DIR, RUNTIME, WEAK_BM25, WEAK_COSINE
from .store import atomic_write

SECTION_NAMES = {"S1": "Scope", "S2": "Conditions", "S3": "Rules", "S4": "Exceptions"}
PASSAGE_SECTIONS = ("S2", "S3", "S4")  # Scope and the metadata header add little for answering
STOP_WORDS = set("a an and are as at be but by can do for from has have i if in is it its me my "
                 "not of on or our so that the their them then there this to was we were will with "
                 "you your please hi hello thanks".split())


@dataclass(frozen=True)
class Policy:
    """One policy file: ID, version, title and its four sections."""
    id: str
    version: str
    title: str
    sections: dict

    def passage(self):
        """Text returned to the model: Conditions + Rules + Exceptions, under a header with ID and version."""
        body = "\n".join(f"{SECTION_NAMES[s]}: {self.sections[s]}" for s in PASSAGE_SECTIONS)
        return f"{self.id} v{self.version}: {self.title}\n{body}"


@dataclass(frozen=True)
class Chunk:
    """One searchable section. The policy title is prepended so a short section still says what it is about."""
    policy_id: str
    section: str
    text: str


def load_policies(policy_dir=POLICY_DIR):
    """Parse policies/*.md into Policy objects (header lines + '## S1 Scope' style sections)."""
    policies = []
    for path in sorted(policy_dir.glob("POL-*.md")):
        text = path.read_text()
        title = text.splitlines()[0].lstrip("# ").strip()
        version = re.search(r'^version: "([^"]+)"', text, re.M).group(1)
        sections = {s: " ".join(body.split()) for s, body in re.findall(r"^## (S\d) [^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)}
        policies.append(Policy(path.stem, version, title, sections))
    return policies


def make_chunks(policies):
    """All four sections of every policy, each labelled with its policy and section name."""
    return [Chunk(p.id, s, f"{p.title}. {SECTION_NAMES[s]}: {text}") for p in policies for s, text in p.sections.items()]


def tokenize(text):
    """Lowercase words for BM25, without stop words. 'final-sale' -> ['final', 'sale']."""
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP_WORDS]


class Bm25Index:
    """Keyword search: scores rise with shared words, rare words counting more."""

    def __init__(self, chunks):
        self.bm25 = BM25Okapi([tokenize(c.text) for c in chunks])

    def scores(self, query):
        """One score per chunk, same order as the chunk list."""
        return np.asarray(self.bm25.get_scores(tokenize(query)))


class EmbeddingIndex:
    """Meaning search: cosine similarity between the query vector and each chunk vector.
    Vectors are cached in runtime/kb_index.json and re-embedded only when a section's text changes."""

    def __init__(self, chunks, embed_fn, cache_path=RUNTIME / "kb_index.json"):
        self.embed_fn = embed_fn
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        vectors = cache.get(EMBED_MODEL, {})  # a different embedding model means a fresh cache
        keys = [hashlib.sha256(c.text.encode()).hexdigest()[:20] for c in chunks]
        missing = [i for i, k in enumerate(keys) if k not in vectors]
        if missing:
            for i, vec in zip(missing, embed_fn([chunks[i].text for i in missing])):
                vectors[keys[i]] = vec
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(cache_path, json.dumps({EMBED_MODEL: {k: vectors[k] for k in keys}}))
        self.newly_embedded = len(missing)
        matrix = np.asarray([vectors[k] for k in keys])
        self.matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    def scores(self, query):
        """Cosine similarity per chunk: 1.0 same meaning, near 0 unrelated."""
        q = np.asarray(self.embed_fn([query])[0])
        return self.matrix @ (q / np.linalg.norm(q))


class Retriever:
    """kb_lookup's engine. Tries embeddings first and falls back to BM25 if they fail or are switched off."""

    def __init__(self, embed_fn=None, policy_dir=POLICY_DIR, cache_path=RUNTIME / "kb_index.json"):
        self.policies = {p.id: p for p in load_policies(policy_dir)}
        self.chunks = make_chunks(self.policies.values())
        self.bm25 = Bm25Index(self.chunks)
        self.embeddings = EmbeddingIndex(self.chunks, embed_fn, cache_path) if embed_fn else None
        self.embedding_outages = 0  # set from a ticket's injected failure: -1 = always down

    def __call__(self, query, k=KB_MAX_K):
        """Lets a Retriever be plugged straight into the tools' Context."""
        return self.search(query, k)

    def search(self, query, k=KB_MAX_K, method="auto"):
        """Top k distinct policies for a query, with scores, method used and a weak-evidence flag."""
        scores, used, fallback_reason = None, "bm25", None
        if method in ("auto", "embeddings") and self.embeddings is not None:
            try:
                if self.embedding_outages:
                    self.embedding_outages -= 1 if self.embedding_outages > 0 else 0
                    raise ConnectionError("simulated embedding outage")
                scores, used = self.embeddings.scores(query), "embeddings"
            except Exception as e:  # outage or API error: fall back, and say so
                if method == "embeddings":
                    raise
                fallback_reason = f"{type(e).__name__}: {e}"[:120]
        if scores is None:
            scores = self.bm25.scores(query)

        ranked = self._rank_policies(scores)[:k]
        passages, used_chars = [], 0
        for policy_id, score, section in ranked:
            room = KB_MAX_CHARS - used_chars
            if room < 200:
                break
            text = self.policies[policy_id].passage()
            if len(text) > room:
                text = text[: room - 1] + "…"  # lowest-ranked passage loses its end (Exceptions) first
            passages.append({"policy_id": policy_id, "version": self.policies[policy_id].version,
                             "title": self.policies[policy_id].title, "score": round(score, 4),
                             "best_section": SECTION_NAMES[section], "text": text})
            used_chars += len(text)
        best = ranked[0][1] if ranked else 0.0
        weak_mark = WEAK_COSINE if used == "embeddings" else WEAK_BM25
        return {"method": used, "fallback_reason": fallback_reason, "best_score": round(best, 4),
                "weak_evidence": best < weak_mark, "chars": used_chars, "passages": passages}

    def _rank_policies(self, scores):
        """Each policy's best section score, highest first: [(policy_id, score, section), ...]."""
        best = {}
        for chunk, score in zip(self.chunks, scores):
            if chunk.policy_id not in best or score > best[chunk.policy_id][0]:
                best[chunk.policy_id] = (float(score), chunk.section)
        return sorted(((pid, s, sec) for pid, (s, sec) in best.items()), key=lambda r: -r[1])
