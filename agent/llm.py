"""Course models through Portkey (OpenAI-compatible API). The key is read from .env, never written in code."""

import os
from functools import lru_cache

from openai import OpenAI

from .config import EMBED_MODEL, PORTKEY_BASE_URL


@lru_cache(maxsize=1)
def client():
    """One shared OpenAI client pointed at Portkey."""
    key = os.getenv("PORTKEY_API_KEY")
    if not key:
        raise RuntimeError("PORTKEY_API_KEY is not set; add it to .env in the repo root")
    return OpenAI(api_key=key, base_url=PORTKEY_BASE_URL)


def embed(texts):
    """Embed a list of texts in one request; returns one 1,536-number vector per text, in order."""
    response = client().embeddings.create(model=EMBED_MODEL, input=list(texts))
    return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
